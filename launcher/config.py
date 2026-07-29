"""Конфигурация виртуального устройства (сохраняется в JSON)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .util import sanitize_avd_name

# Источник образа → (папка-тег в system-images, tag.id в config.ini, подпись)
SOURCE_INFO = {
    "play": ("google_apis_playstore", "google_apis_playstore", "Google Play"),
    "apis": ("google_apis", "google_apis", "Google APIs"),
    "aosp": ("default", "default", "AOSP"),
}


@dataclass
class ImageSpec:
    """Официальный системный образ Google."""
    api: int
    source: str = "play"          # play | apis | aosp
    abi: str = "x86_64"
    url: str = ""
    sha1: str = ""
    size: int = 0
    revision: str = ""
    api_str: str = ""             # исходная строка api-level, бывает "37.0"

    @property
    def api_label(self) -> str:
        return self.api_str or str(self.api)

    @property
    def tag_dir(self) -> str:
        return SOURCE_INFO.get(self.source, SOURCE_INFO["play"])[0]

    @property
    def tag_id(self) -> str:
        return SOURCE_INFO.get(self.source, SOURCE_INFO["play"])[1]

    @property
    def tag_display(self) -> str:
        return SOURCE_INFO.get(self.source, SOURCE_INFO["play"])[2]

    def sysdir(self, sdk_root: Path) -> Path:
        return (sdk_root / "system-images" / f"android-{self.api_label}"
                / self.tag_dir / self.abi)

    def is_downloaded(self, sdk_root: Path) -> bool:
        d = self.sysdir(sdk_root)
        if (d / "system.img").exists() and (d / "ramdisk.img").exists():
            return True
        # Самолечение распаковок, где всё ушло во вложенную папку (например
        # x86_64\x86_64\system.img, как делала версия 1.0) — поднимаем вверх.
        self.repair_layout(sdk_root)
        return (d / "system.img").exists() and (d / "ramdisk.img").exists()

    def repair_layout(self, sdk_root: Path) -> None:
        """Поднять содержимое единственной вложенной папки с образом
        на уровень sysdir. Безопасно: ничего не перезаписывает."""
        d = self.sysdir(sdk_root)
        if not d.is_dir():
            return
        entries = list(d.iterdir())
        dirs = [e for e in entries if e.is_dir()]
        files = [e for e in entries if e.is_file()]
        target: Path | None = None
        if len(dirs) == 1 and not files:                 # одна подпапка, файлов нет
            target = dirs[0]
        elif (d / self.abi / "system.img").exists():     # явная вложенная <abi>/
            target = d / self.abi
        if not target or not (target / "system.img").exists():
            return
        for item in list(target.iterdir()):
            dest = d / item.name
            if not dest.exists():
                try:
                    item.rename(dest)
                except OSError:
                    pass
        try:
            target.rmdir()  # уйдёт, только если опустела
        except OSError:
            pass


@dataclass
class EmuConfig:
    """Всё, что пользователь выбрал в мастере."""
    avd_name: str = "Pixel7Pro"
    device_id: str = "pixel_7_pro"
    width: int = 1440
    height: int = 3120
    density: int = 560
    ram_mb: int = 8192
    cores: int = 4
    data_gb: int = 16
    sdcard_mb: int = 512            # 0 = выключена
    gpu: str = "auto"               # auto | host | swiftshader_indirect | angle_indirect
    boot: str = "fast"              # fast (снапшот) | cold | wipe (сброс данных)
    camera: bool = True
    mic: bool = True
    gps: bool = True
    extra_flags: str = ""
    image: ImageSpec = field(default_factory=lambda: ImageSpec(api=35))
    created: str = ""

    def normalize(self) -> None:
        self.avd_name = sanitize_avd_name(self.avd_name)
        self.ram_mb = max(1024, min(int(self.ram_mb), 32768))
        self.cores = max(1, min(int(self.cores), 32))
        self.width = max(240, min(int(self.width), 4096))
        self.height = max(320, min(int(self.height), 4096))
        self.density = max(120, min(int(self.density), 960))
        self.data_gb = max(2, min(int(self.data_gb), 256))

    def save(self, configs_dir: Path) -> Path:
        self.normalize()
        if not self.created:
            self.created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        configs_dir.mkdir(parents=True, exist_ok=True)
        path = configs_dir / f"{self.avd_name}.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "EmuConfig":
        data = json.loads(path.read_text(encoding="utf-8"))
        img = data.pop("image", {}) or {}
        cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        cfg.image = ImageSpec(**{k: v for k, v in img.items()
                                 if k in ImageSpec.__dataclass_fields__})
        return cfg
