"""Загрузки больших файлов и мост к нативному C++-ядру (pixemu-core.exe).

Если скомпилированное ядро найдено — скачивает оно (WinHTTP, быстро, с прогрессом),
иначе используется запасной вариант на чистом Python (медленнее, но работает везде).
Прогресс передаётся через callback progress(done_bytes, total_bytes).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .util import ROOT, AppError, is_windows

Progress = Callable[[int, int], None]

_CORE_CANDIDATES = (
    ROOT / "native" / "bin" / "pixemu-core.exe",
    ROOT / "native" / "bin" / "pixemu-core",
)


def core_path() -> Path | None:
    cands: list[Path] = []
    if getattr(sys, "frozen", False):          # сборка PyInstaller
        meipass = Path(getattr(sys, "_MEIPASS", ROOT))
        cands.append(meipass / "native" / "bin" / "pixemu-core.exe")
        # рядом с exe (портативный вариант: ядро положили рядом вручную)
        cands.append(Path(sys.executable).resolve().parent
                     / "native" / "bin" / "pixemu-core.exe")
    cands.extend(_CORE_CANDIDATES)
    for p in cands:
        if p.exists():
            return p
    return None


def core_available() -> bool:
    return core_path() is not None


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_native(url: str, dest: Path, progress: Progress | None) -> None:
    core = core_path()
    assert core is not None
    proc = subprocess.Popen(
        [str(core), "download", url, str(dest)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    assert proc.stdout is not None
    errors: list[str] = []
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("DL ") and progress:
            try:
                _, done, total = line.split()
                progress(int(done), int(total))
            except ValueError:
                pass
        elif line.startswith("ERR "):
            errors.append(line[4:])
    code = proc.wait()
    if code != 0:
        raise AppError("C++-ядро: ошибка загрузки.\n" + "\n".join(errors[-3:]))


def _download_python(url: str, dest: Path, progress: Progress | None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "PixEmuStudio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except Exception as exc:  # noqa: BLE001
        raise AppError(f"Ошибка загрузки:\n{url}\n\n{exc}") from exc


def download(url: str, dest: Path, sha1: str = "",
             progress: Progress | None = None, log=None) -> Path:
    """Скачать url → dest с проверкой SHA-1 (если задан). Возобновление не требуется:
    при неудаче частичный файл удаляется."""
    if log:
        how = "C++-ядро (WinHTTP)" if core_available() else "Python (urllib)"
        log(f"Загрузка: {url}\n  через: {how}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        if core_available():
            _download_native(url, tmp, progress)
        else:
            _download_python(url, tmp, progress)
        if sha1:
            if log:
                log("Проверка SHA-1…")
            digest = sha1_of(tmp)
            if digest.lower() != sha1.lower():
                raise AppError(f"Контрольная сумма не совпала!\n"
                               f"ожидалось: {sha1}\nполучено:   {digest}")
        tmp.replace(dest)
        return dest
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _common_root_prefix(names: list[str]) -> str | None:
    """Если ВСЕ записи архива лежат в одной верхней папке ('x86_64/…'),
    возвращает её как префикс ('x86_64/'). Иначе None."""
    prefix: str | None = None
    for n in names:
        parts = n.split("/")
        if len(parts) < 2:
            return None  # файл прямо в корне архива — ничего не срезаем
        p = parts[0] + "/"
        if prefix is None:
            prefix = p
        elif prefix != p:
            return None
    return prefix


def unzip(zip_path: Path, dest_dir: Path, progress: Progress | None = None,
          log=None, strip_root: bool = False) -> None:
    """Распаковка с прогрессом по байтам (образы ~2+ ГБ).

    strip_root=True — срезать общую верхнюю папку архива: системные образы
    Google упакованы как 'x86_64/system.img', а нужно 'system.img' на месте.
    """
    if log:
        log(f"Распаковка {zip_path.name} → {dest_dir}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        prefix = _common_root_prefix([i.filename for i in infos]) if strip_root else None
        total = sum(i.file_size for i in infos) or 1
        done = 0
        for info in infos:
            rel = info.filename
            if prefix and rel.startswith(prefix):
                rel = rel[len(prefix):]
            if not rel or rel.endswith("/"):  # каталог
                if rel:
                    (dest_dir / rel).mkdir(parents=True, exist_ok=True)
                continue
            target = dest_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
