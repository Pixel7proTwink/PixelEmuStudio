"""Создание AVD (Android Virtual Device) — формат полностью совместим
с официальным эмулятором Google: <name>.avd/config.ini + <name>.ini."""
from __future__ import annotations

import os
from pathlib import Path

from .config import EmuConfig


def _ini_path(path: Path | str) -> str:
    """Путь Windows с одинарными обратными слешами — БЕЗ экранирования.

    Парсер ini у эмулятора Google НЕ раскрывает escape-последовательности
    (читал 'C\\:\\\\Users\\\\…' буквально → FATAL Broken AVD system path),
    поэтому пишем пути как есть, в стиле avdmanager."""
    return str(path).replace("/", "\\")


def avd_dir(sdk_root: Path, avd_name: str) -> Path:
    return sdk_root / "avds" / f"{avd_name}.avd"


# ABI образа → архитектура CPU для ключа hw.cpu.arch.
# ВАЖНО: именно этот ключ читает QEMU2 при старте; без него эмулятор
# предполагает 'arm' и падает с «CPU Architecture 'arm' is not supported».
_ABI_TO_ARCH = {
    "x86_64": "x86_64",
    "x86": "x86",
    "arm64-v8a": "arm64",
    "armeabi-v7a": "arm",
}


def ensure_avd(cfg: EmuConfig, sdk_root: Path) -> Path:
    """(Пере)создаёт файлы AVD по конфигу. Вызывается перед каждым запуском."""
    d = avd_dir(sdk_root, cfg.avd_name)
    d.mkdir(parents=True, exist_ok=True)

    sysdir = cfg.image.sysdir(sdk_root)
    # ОТНОСИТЕЛЬНЫЙ путь к образу, как пишет avdmanager: эмулятор сам
    # присоединит ANDROID_SDK_ROOT. Абсолютный экранированный путь он
    # трактует как относительный и ломается (см. баг 1.0.2).
    sysdir_rel = (f"system-images\\android-{cfg.image.api_label}"
                  f"\\{cfg.image.tag_dir}\\{cfg.image.abi}\\")
    cam = "emulated" if cfg.camera else "none"
    sdcard_on = cfg.sdcard_mb > 0
    arch = _ABI_TO_ARCH.get(cfg.image.abi, "x86_64")

    keys = [
        ("AvdId", cfg.avd_name),
        ("avd.ini.displayname", cfg.avd_name),
        ("avd.ini.encoding", "UTF-8"),
        ("abi.type", cfg.image.abi),
        ("hw.cpu.arch", arch),
        ("tag.id", cfg.image.tag_id),
        ("tag.display", cfg.image.tag_display),
        ("image.sysdir.1", sysdir_rel),
        ("hw.device.manufacturer", "Google"),
        ("hw.device.name", cfg.device_id),
        ("hw.lcd.width", str(cfg.width)),
        ("hw.lcd.height", str(cfg.height)),
        ("hw.lcd.density", str(cfg.density)),
        ("hw.displayWidth", str(cfg.width)),
        ("hw.displayHeight", str(cfg.height)),
        ("hw.ramSize", str(cfg.ram_mb)),
        ("hw.cpu.ncore", str(cfg.cores)),
        ("vm.heapSize", "512"),
        ("disk.dataPartition.size", str(cfg.data_gb * (1 << 30))),  # байты, как у avdmanager
        ("hw.sdCard", "yes" if sdcard_on else "no"),
        ("hw.accelerometer", "yes"),
        ("hw.sensors.orientation", "yes"),
        ("hw.sensors.proximity", "yes"),
        ("hw.audioInput", "yes" if cfg.mic else "no"),
        ("hw.audioOutput", "yes"),
        ("hw.battery", "yes"),
        ("hw.camera.back", cam),
        ("hw.camera.front", cam),
        ("hw.gps", "yes" if cfg.gps else "no"),
        ("hw.gpu.enabled", "yes"),
        ("hw.gpu.mode", cfg.gpu),
        ("hw.keyboard", "yes"),
        ("hw.mainKeys", "no"),
        ("hw.dPad", "no"),
        ("hw.trackBall", "no"),
        ("hw.arc", "false"),
        ("hw.initialOrientation", "Portrait"),
        # НЕ '_no_skin'! Новые эмуляторы считают его неизвестным скином и
        # падают с 'unknown skin name' в оконном режиме. Динамический скин
        # по размеру (WxH) эмулятор создаёт сам, без пакета skins.
        ("skin.name", f"{cfg.width}x{cfg.height}"),
        ("skin.dynamic", "yes"),
        ("showDeviceFrame", "no"),
        ("runtime.network.speed", "full"),
        ("runtime.network.latency", "none"),
        ("fastboot.forceFastBoot", "yes" if cfg.boot == "fast" else "no"),
        ("fastboot.forceColdBoot", "yes" if cfg.boot == "cold" else "no"),
        ("fastboot.forceChosenSnapshotBoot", "no"),
        ("PlayStore.enabled", "true" if cfg.image.source == "play" else "false"),
    ]
    if sdcard_on:
        keys.append(("sdcard.size", f"{cfg.sdcard_mb}M"))

    lines = [f"{k}={v}" for k, v in keys]
    (d / "config.ini").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # <name>.ini — указатель на папку AVD (для -avd)
    ini = sdk_root / "avds" / f"{cfg.avd_name}.ini"
    ini.write_text(
        "avd.ini.encoding=UTF-8\n"
        f"path={_ini_path(d)}\n"
        f"path.rel=avds\\{cfg.avd_name}.avd\n"
        f"target=android-{cfg.image.api}\n",
        encoding="utf-8",
    )
    return d


def remove_avd(cfg: EmuConfig, sdk_root: Path) -> None:
    import shutil

    shutil.rmtree(avd_dir(sdk_root, cfg.avd_name), ignore_errors=True)
    (sdk_root / "avds" / f"{cfg.avd_name}.ini").unlink(missing_ok=True)
    cfg_path = sdk_root / "configs" / f"{cfg.avd_name}.json"
    cfg_path.unlink(missing_ok=True)


def avd_environment(sdk_root: Path) -> dict:
    """Переменные окружения, чтобы официальный эмулятор нашёл AVD и образы."""
    env = dict(os.environ)
    env["ANDROID_SDK_ROOT"] = str(sdk_root)
    env["ANDROID_HOME"] = str(sdk_root)
    env["ANDROID_SDK_HOME"] = str(sdk_root)
    env["ANDROID_AVD_HOME"] = str(sdk_root / "avds")
    return env
