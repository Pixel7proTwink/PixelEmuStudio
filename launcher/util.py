"""Общие утилиты: пути, ресурсы ПК, форматирование."""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # корень проекта
# В сборке PyInstaller ROOT указывает внутрь распакованного бандла (_MEIPASS),
# файлы данных (ядро, иконка) доступны оттуда вполне легально.


class AppError(Exception):
    """Ошибка с понятным пользователю сообщением."""


def is_windows() -> bool:
    return os.name == "nt"


def default_sdk_root() -> Path:
    """Куда ставим движок/образы/AVD (по умолчанию %LOCALAPPDATA%\\PixEmuStudio)."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "PixEmuStudio"
    return Path.home() / ".pixemu-studio"


# Настройки — в папке данных пользователя (корень проекта в exe-сборке
# доступен только для чтения!).
SETTINGS_FILE = default_sdk_root() / "settings.json"
LEGACY_SETTINGS_FILE = ROOT / "settings.local.json"     # старое имя (1.0.x, dev)


def ensure_dirs(sdk_root: Path) -> None:
    for sub in ("configs", "avds", "system-images", "downloads", "cache",
                "emulator", "platform-tools"):
        (sdk_root / sub).mkdir(parents=True, exist_ok=True)


def human_size(num: int | float) -> str:
    val = float(num)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if val < 1024 or unit == "ТБ":
            if unit in ("Б", "КБ"):
                return f"{val:.0f} {unit}"
            return f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} ТБ"


def host_ram_mb() -> int:
    """Оперативная память хоста, МБ."""
    if is_windows():
        try:
            import ctypes

            class MEMSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = MEMSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
                return st.ullTotalPhys // (1024 * 1024)
        except Exception:
            pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        return 8192


def cpu_count() -> int:
    return os.cpu_count() or 4


def disk_free_gb(path: Path) -> float:
    usage = shutil.disk_usage(str(path if path.exists() else path.anchor or "."))
    return usage.free / (1024 ** 3)


def whpx_available() -> bool:
    """Доступен ли Windows Hypervisor Platform (аппаратное ускорение эмулятора)."""
    if not is_windows():
        return False
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    dlls = ("WinHvPlatform.dll", "WinHvEmulation.dll")
    return all((Path(windir) / "System32" / d).exists() for d in dlls)


_BAD_CHARS = re.compile(r"[^A-Za-z0-9._\- ]+")

# Эмулятор Google плохо переносит не-ASCII пути → транслитерируем кириллицу.
_RU = ("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")
_EN = ("a,b,v,g,d,e,e,zh,z,i,y,k,l,m,n,o,p,r,s,t,u,f,h,ts,ch,sh,shch,"
       "',y,',e,yu,ya").split(",")
TRANSLIT = dict(zip(_RU, _EN)) | dict(zip(_RU.upper(), (e.capitalize() for e in _EN)))


def sanitize_avd_name(name: str) -> str:
    """Имя AVD — оно станет именем файлов/папок и аргументом -avd.
    Только латиница/цифры (кириллица транслитерируется)."""
    name = "".join(TRANSLIT.get(ch, ch) for ch in name.strip())
    name = _BAD_CHARS.sub("_", name)
    name = name.replace(" ", "_").strip("._-")
    return name or "PixelAVD"
