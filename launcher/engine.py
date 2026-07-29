"""Управление официальным эмуляторным движком Google и запуск AVD."""
from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .avd import avd_environment, ensure_avd
from .config import EmuConfig
from .netio import download, unzip
from .repo import PLATFORM_TOOLS_URL, load_emulator_package
from .util import AppError, disk_free_gb, host_ram_mb, is_windows, whpx_available


def engine_exe(sdk_root: Path) -> Path:
    return sdk_root / "emulator" / ("emulator.exe" if is_windows() else "emulator")


def engine_installed(sdk_root: Path) -> bool:
    return engine_exe(sdk_root).exists()


def install_engine(sdk_root: Path, progress=None, log=None) -> str:
    """Скачать и установить официальный Android Emulator для Windows.
    Возвращает версию."""
    pkg = load_emulator_package(sdk_root / "cache")
    if log:
        log(f"Движок: версия {pkg['version']}, {pkg['size'] // (1 << 20)} МБ, "
            f"{pkg['url']}")
    dest = sdk_root / "downloads" / "emulator.zip"
    download(pkg["url"], dest, pkg["sha1"], progress=progress, log=log)
    unzip(dest, sdk_root / "downloads" / "emulator_unpacked", progress=progress,
          log=log)
    src = sdk_root / "downloads" / "emulator_unpacked" / "emulator"
    if not src.exists():
        raise AppError("Архив движка не содержит папку 'emulator'. "
                       "Структура пакета изменилась.")
    target = sdk_root / "emulator"
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(src), str(target))
    shutil.rmtree(sdk_root / "downloads" / "emulator_unpacked", ignore_errors=True)
    dest.unlink(missing_ok=True)
    if not engine_installed(sdk_root):
        raise AppError("Движок установлен, но emulator.exe не найден.")
    return pkg["version"]


def install_image(cfg: EmuConfig, sdk_root: Path, progress=None, log=None) -> None:
    """Скачать и ПРАВИЛЬНО распаковать системный образ (со срезанием общей
    папки архива x86_64\\), затем проверить результат. Выбрасывает AppError
    с диагностикой, если system.img не появился на месте."""
    img = cfg.image
    if img.is_downloaded(sdk_root):          # включая самолечение старых распаковок
        if log:
            log("Системный образ уже установлен.")
        return
    if not img.url:
        raise AppError("В конфигурации нет URL образа. Пересоздайте эмулятор "
                       "через мастер (образы выбираются со списка Google).")
    zip_path = sdk_root / "downloads" / Path(img.url).name
    download(img.url, zip_path, img.sha1, progress=progress, log=log)
    unzip(zip_path, img.sysdir(sdk_root), progress=progress, log=log,
          strip_root=True)
    zip_path.unlink(missing_ok=True)
    if not img.is_downloaded(sdk_root):
        d = img.sysdir(sdk_root)
        found = []
        if d.exists():
            found = [str(p.relative_to(d)) for p in list(d.rglob("*"))[:20]]
        raise AppError("Образ распакован, но system.img не найден там, где "
                       "ожидалось. Содержимое папки:\n" + "\n".join(found))


def install_platform_tools(sdk_root: Path, progress=None, log=None) -> Path:
    """platform-tools (adb) — для установки APK и отладки."""
    dest = sdk_root / "downloads" / "platform-tools.zip"
    download(PLATFORM_TOOLS_URL, dest, progress=progress, log=log)
    unzip(dest, sdk_root, progress=progress, log=log)
    dest.unlink(missing_ok=True)
    adb = sdk_root / "platform-tools" / ("adb.exe" if is_windows() else "adb")
    if not adb.exists():
        raise AppError("platform-tools распакованы, но adb не найден.")
    return adb


def accel_report(sdk_root: Path) -> str:
    """Диагностика ускорения и ПК (кнопка «Проверить ускорение»)."""
    if whpx_available():
        whpx_note = "доступен"
    else:
        whpx_note = ("НЕ найден — включите компонент "
                     "«Платформа гипервизора Windows» и «Платформа виртуальной "
                     "машины», затем перезагрузите ПК")
    lines = [
        f"ОЗУ хоста: {host_ram_mb()} МБ",
        f"Свободно на диске SDK: {disk_free_gb(sdk_root):.1f} ГБ",
        f"WHPX (гипервизор Windows): {whpx_note}",
        "",
    ]
    if engine_installed(sdk_root):
        try:
            res = subprocess.run(
                [str(engine_exe(sdk_root)), "-accel-check"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60,
                env=avd_environment(sdk_root),
            )
            lines.append("Отчёт движка (-accel-check):")
            lines.append((res.stdout + res.stderr).strip() or "(пустой вывод)")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Не удалось запустить -accel-check: {exc}")
    else:
        lines.append("Движок ещё не установлен.")
    return "\n".join(lines)


def launch(cfg: EmuConfig, sdk_root: Path, log=None) -> subprocess.Popen:
    """Запуск эмулятора. Возвращает Popen (stdout → в чтение лога)."""
    if not engine_installed(sdk_root):
        raise AppError("Движок не установлен. Откройте «Настройки» → "
                       "«Скачать движок эмулятора».")
    if not cfg.image.is_downloaded(sdk_root):
        raise AppError("Системный образ не скачан. Скачайте его в мастере "
                       "или нажмите «Запустить» ещё раз после загрузки.")
    ensure_avd(cfg, sdk_root)

    # Только гарантированные флаги: неизвестный флаг эмулятор отвергает с
    # мгновенным выходом. RAM/ядра берутся из config.ini (hw.ramSize/hw.cpu.ncore).
    # -no-metrics — отключает предупреждение/диалог о сборе статистики.
    cmd = [str(engine_exe(sdk_root)), "-avd", cfg.avd_name,
           "-gpu", cfg.gpu, "-verbose", "-no-metrics"]
    if cfg.boot == "wipe":
        cmd.append("-wipe-data")
    elif cfg.boot == "cold":
        cmd.append("-no-snapshot-load")
    if cfg.extra_flags.strip():
        cmd += shlex.split(cfg.extra_flags)

    if log:
        log("Запуск: " + " ".join(cmd))
    kwargs = {}
    if is_windows():
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    proc = subprocess.Popen(
        cmd, env=avd_environment(sdk_root),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", **kwargs,
    )
    return proc
