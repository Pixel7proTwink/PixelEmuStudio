// pixemu-core — нативное ядро PixelEmu Studio (C++17, MinGW / MSYS2).
//
// Задачи ядра (всё, что в Python было бы медленным или неудобным):
//   download <url> <file>   — быстрая HTTPS-загрузка через WinHTTP с прогрессом
//                             (строки "DL <done> <total>" в stdout, редиректы 3xx);
//   sha1 <file>             — SHA-1 через BCrypt (строка "SHA1 <hex>");
//   run -- <exe> [args...]  — запуск процесса через CreateProcess с пробросом
//                             вывода (строка "EXIT <code>" в конце).
//
// Сборка (MSYS2 UCRT64):   g++ -std=c++17 -O2 -o bin/pixemu-core.exe src/main.cpp -lwinhttp -lbcrypt
#include <windows.h>
#include <winhttp.h>
#include <bcrypt.h>

#include <cstdio>
#include <string>
#include <vector>

using std::string;
using std::wstring;

// ---------- UTF-8 <-> UTF-16 ----------

static string U(const wstring& ws) {
    if (ws.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, ws.c_str(), -1, nullptr, 0, nullptr, nullptr);
    string r(size_t(n - 1), 0);
    WideCharToMultiByte(CP_UTF8, 0, ws.c_str(), -1, r.data(), n, nullptr, nullptr);
    return r;
}

static void print_err(const wstring& msg) {
    fprintf(stdout, "ERR %s\n", U(msg).c_str());
    fflush(stdout);
}

// ---------- SHA-1 (BCrypt) ----------

static string sha1_file(const wstring& path) {
    HANDLE f = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (f == INVALID_HANDLE_VALUE) return "";

    BCRYPT_ALG_HANDLE alg = nullptr;
    BCRYPT_HASH_HANDLE h = nullptr;
    string hex;
    if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA1_ALGORITHM, nullptr, 0) != 0) {
        CloseHandle(f);
        return "";
    }
    DWORD obj_len = 0, copied = 0;
    BCryptGetProperty(alg, BCRYPT_OBJECT_LENGTH, (PUCHAR)&obj_len, sizeof(obj_len),
                      &copied, 0);
    std::vector<BYTE> obj(obj_len);
    if (BCryptCreateHash(alg, &h, obj.data(), obj_len, nullptr, 0, 0) == 0) {
        BYTE buf[1 << 16];
        DWORD got = 0;
        while (ReadFile(f, buf, sizeof(buf), &got, nullptr) && got)
            BCryptHashData(h, buf, got, 0);
        BYTE digest[20];
        if (BCryptFinishHash(h, digest, sizeof(digest), 0) == 0) {
            static const char* H = "0123456789abcdef";
            hex.assign(40, '0');
            for (int i = 0; i < 20; ++i) {
                hex[size_t(2 * i)] = H[digest[i] >> 4];
                hex[size_t(2 * i + 1)] = H[digest[i] & 15];
            }
        }
        BCryptDestroyHash(h);
    }
    BCryptCloseAlgorithmProvider(alg, 0);
    CloseHandle(f);
    return hex;
}

// ---------- HTTPS-загрузка (WinHTTP) ----------

static bool download_once(HINTERNET session, const wstring& url, FILE* out,
                          unsigned long long& done, unsigned long long& total,
                          wstring& redirect_to, wstring& err) {
    URL_COMPONENTS uc{};
    uc.dwStructSize = sizeof(uc);
    wchar_t host[256]{}, path[4096]{};
    uc.lpszHostName = host;
    uc.dwHostNameLength = 256;
    uc.lpszUrlPath = path;
    uc.dwUrlPathLength = 4096;
    if (!WinHttpCrackUrl(url.c_str(), 0, 0, &uc)) {
        err = L"WinHttpCrackUrl failed: " + url;
        return false;
    }

    HINTERNET con = WinHttpConnect(session, host, uc.nPort, 0);
    if (!con) { err = L"WinHttpConnect failed"; return false; }

    DWORD flags = (uc.nScheme == INTERNET_SCHEME_HTTPS) ? WINHTTP_FLAG_SECURE : 0;
    HINTERNET req = WinHttpOpenRequest(con, L"GET", path, nullptr,
                                       WINHTTP_NO_REFERER,
                                       WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
    if (!req) { err = L"WinHttpOpenRequest failed"; WinHttpCloseHandle(con); return false; }

    bool ok = WinHttpSendRequest(req, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                                 WINHTTP_NO_REQUEST_DATA, 0, 0, 0)
              && WinHttpReceiveResponse(req, nullptr);
    if (!ok) {
        err = L"HTTP request failed (Win32 " + std::to_wstring(GetLastError()) + L")";
        WinHttpCloseHandle(req);
        WinHttpCloseHandle(con);
        return false;
    }

    DWORD status = 0, sz = sizeof(status);
    WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                        nullptr, &status, &sz, nullptr);

    if (status >= 300 && status < 400) {  // редирект
        wchar_t loc[4096]{};
        DWORD loc_len = sizeof(loc) - sizeof(wchar_t);
        if (WinHttpQueryHeaders(req, WINHTTP_QUERY_LOCATION, nullptr, loc, &loc_len,
                                nullptr))
            redirect_to = loc;
        WinHttpCloseHandle(req);
        WinHttpCloseHandle(con);
        return true;
    }
    if (status != 200) {
        err = L"HTTP status " + std::to_wstring(status);
        WinHttpCloseHandle(req);
        WinHttpCloseHandle(con);
        return false;
    }

    // Content-Length (текстовый запрос — поддерживает >4 ГБ при необходимости)
    wchar_t len_buf[64]{};
    DWORD len_len = sizeof(len_buf) - sizeof(wchar_t);
    if (WinHttpQueryHeaders(req, WINHTTP_QUERY_CONTENT_LENGTH, nullptr, len_buf,
                            &len_len, nullptr))
        total = _wcstoui64(len_buf, nullptr, 10);

    BYTE chunk[1 << 16];
    DWORD got = 0;
    ULONGLONG last_tick = 0;
    while (WinHttpReadData(req, chunk, sizeof(chunk), &got) && got) {
        if (fwrite(chunk, 1, got, out) != got) {
            err = L"disk write failed";
            WinHttpCloseHandle(req);
            WinHttpCloseHandle(con);
            return false;
        }
        done += got;
        ULONGLONG tick = GetTickCount64();
        if (tick - last_tick > 200) {  // прогресс не чаще 5 раз/сек
            last_tick = tick;
            fprintf(stdout, "DL %llu %llu\n", done, total);
            fflush(stdout);
        }
    }
    fprintf(stdout, "DL %llu %llu\n", done, total);
    fflush(stdout);
    WinHttpCloseHandle(req);
    WinHttpCloseHandle(con);
    return true;
}

static int cmd_download(const wstring& url, const wstring& dest) {
    FILE* out = _wfopen(dest.c_str(), L"wb");
    if (!out) { print_err(L"cannot open " + dest); return 1; }

    HINTERNET session = WinHttpOpen(L"PixEmuCore/1.0",
                                    WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
                                    WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session) { print_err(L"WinHttpOpen failed"); fclose(out); return 1; }

    unsigned long long done = 0, total = 0;
    wstring err, current = url;
    bool ok = false;
    for (int hops = 0; hops < 5 && !ok; ++hops) {
        wstring redirect;
        if (!download_once(session, current, out, done, total, redirect, err))
            break;
        if (redirect.empty()) { ok = true; break; }
        current = redirect;  // 3xx → повторяем по Location
    }
    WinHttpCloseHandle(session);
    fclose(out);
    if (!ok) {
        print_err(err.empty() ? L"download failed" : err);
        DeleteFileW(dest.c_str());
        return 1;
    }
    return 0;
}

// ---------- Запуск процесса с пробросом вывода ----------

static wstring quote_arg(const wstring& a) {
    if (a.find_first_of(L" \t\"") == wstring::npos) return a;
    wstring r = L"\"";
    for (wchar_t c : a) {
        if (c == L'"') r += L"\\\"";
        else r += c;
    }
    return r + L"\"";
}

static int cmd_run(const std::vector<wstring>& args) {
    if (args.empty()) { print_err(L"run: no command"); return 2; }
    wstring cmdline;
    for (size_t i = 0; i < args.size(); ++i) {
        if (i) cmdline += L" ";
        cmdline += quote_arg(args[i]);
    }

    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    HANDLE rd = nullptr, wr = nullptr;
    if (!CreatePipe(&rd, &wr, &sa, 0)) { print_err(L"CreatePipe failed"); return 1; }
    SetHandleInformation(rd, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = wr;
    si.hStdError = wr;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);

    std::vector<wchar_t> cmd(cmdline.begin(), cmdline.end());
    cmd.push_back(0);
    PROCESS_INFORMATION pi{};
    BOOL created = CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, TRUE,
                                  CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi);
    CloseHandle(wr);
    if (!created) {
        print_err(L"CreateProcess failed: Win32 " + std::to_wstring(GetLastError()));
        CloseHandle(rd);
        return 1;
    }
    char buf[4096];
    DWORD n = 0;
    while (ReadFile(rd, buf, sizeof(buf), &n, nullptr) && n) {
        fwrite(buf, 1, n, stdout);
        fflush(stdout);
    }
    CloseHandle(rd);
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    fprintf(stdout, "EXIT %lu\n", (unsigned long)code);
    fflush(stdout);
    return (int)code;
}

// ---------- main ----------

int wmain(int argc, wchar_t* argv[]) {
    SetConsoleOutputCP(CP_UTF8);
    if (argc < 2) {
        fprintf(stderr,
                "pixemu-core — нативное ядро PixelEmu Studio\n"
                "  download <url> <file>    скачать с прогрессом (DL done total)\n"
                "  sha1 <file>              SHA-1 файла (SHA1 hex)\n"
                "  run -- <exe> [args...]   запустить процесс, пробросить вывод\n");
        return 2;
    }
    std::vector<wstring> a;
    for (int i = 1; i < argc; ++i) a.emplace_back(argv[i]);

    if (a[0] == L"download" && a.size() == 3) return cmd_download(a[1], a[2]);
    if (a[0] == L"sha1" && a.size() == 2) {
        string hex = sha1_file(a[1]);
        if (hex.empty()) { print_err(L"sha1 failed for " + a[1]); return 1; }
        fprintf(stdout, "SHA1 %s\n", hex.c_str());
        return 0;
    }
    if (a[0] == L"run") {
        std::vector<wstring> rest(a.begin() + 1, a.end());
        if (!rest.empty() && rest[0] == L"--") rest.erase(rest.begin());
        return cmd_run(rest);
    }
    fprintf(stderr, "unknown command\n");
    return 2;
}
