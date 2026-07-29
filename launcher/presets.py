"""Аппаратные пресеты устройств (как в Device Manager Android Studio).

Пресет задаёт параметры «железа», которые пользователь потом может изменить
в мастере. SoC всегда эмулируется как x86_64 (или arm64 на ARM-ПК) — Tensor
на эмуляторе не воспроизводится, это нормально.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DevicePreset:
    dev_id: str          # идентификатор профиля (как hw.device.name в AVD)
    title: str           # отображаемое имя
    width: int           # разрешение экрана, px
    height: int
    density: int         # DPI (как в профиле устройства Android Studio)
    ram_mb: int          # RAM по умолчанию (можно изменить в мастере)
    cores: int           # vCPU по умолчанию
    storage_gb: int      # раздел userdata по умолчанию
    screen_in: float     # диагональ, дюймы (информационно)
    soc: str             # чип реального устройства (информационно)
    note: str = ""


PRESETS: list[DevicePreset] = [
    DevicePreset(
        "pixel_7_pro", "Pixel 7 Pro", 1440, 3120, 560,
        ram_mb=8192, cores=4, storage_gb=16, screen_in=6.7,
        soc="Google Tensor G2 → эмулируется как x86_64",
        note="Флагман: 12 ГБ RAM в железе, 120 Гц. Для AVD рекомендуется 8 ГБ.",
    ),
    DevicePreset(
        "pixel_7", "Pixel 7", 1080, 2400, 420,
        ram_mb=6144, cores=4, storage_gb=12, screen_in=6.3,
        soc="Google Tensor G2 → эмулируется как x86_64",
        note="8 ГБ RAM в железе. Для AVD рекомендуется 6 ГБ.",
    ),
    DevicePreset(
        "pixel_6a", "Pixel 6a", 1080, 2400, 420,
        ram_mb=4096, cores=4, storage_gb=8, screen_in=6.1,
        soc="Google Tensor → эмулируется как x86_64",
        note="6 ГБ RAM в железе. Лёгкий вариант для слабых ПК.",
    ),
    DevicePreset(
        "custom", "Своя конфигурация", 1080, 2340, 440,
        ram_mb=4096, cores=4, storage_gb=8, screen_in=6.0,
        soc="—",
        note="Полностью ручные характеристики.",
    ),
]

DEFAULT_PRESET = PRESETS[0]


def get_preset(dev_id: str) -> DevicePreset:
    for p in PRESETS:
        if p.dev_id == dev_id:
            return p
    return DEFAULT_PRESET
