"""Работа с официальными репозиториями Google (dl.google.com).

Те же XML-манифесты, что читает SDK Manager:
  * system-images  — официальные образы Android (Google Play / Google APIs / AOSP);
  * repository2-3  — пакеты инструментов, в т.ч. официальный Android Emulator.
"""
from __future__ import annotations

import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import ImageSpec
from .util import AppError

ANDROID_ROOT = "https://dl.google.com/android/repository"

CHANNELS: dict[str, tuple[str, str]] = {
    "play": ("Google Play (рекомендуется)",
             f"{ANDROID_ROOT}/sys-img/google_apis_playstore/sys-img2-3.xml"),
    "apis": ("Google APIs (без Play Маркета)",
             f"{ANDROID_ROOT}/sys-img/google_apis/sys-img2-3.xml"),
    "aosp": ("AOSP (чистый Android, без сервисов)",
             f"{ANDROID_ROOT}/sys-img/android/sys-img2-3.xml"),
}

EMULATOR_MANIFEST_URL = f"{ANDROID_ROOT}/repository2-3.xml"
PLATFORM_TOOLS_URL = f"{ANDROID_ROOT}/platform-tools-latest-windows.zip"

API_TO_ANDROID = {28: "9", 29: "10", 30: "11", 31: "12", 32: "12L",
                  33: "13", 34: "14", 35: "15", 36: "16", 37: "17", 38: "18"}

_ABI_PREFERENCE = {"x86_64": 0, "arm64-v8a": 1, "x86": 2, "armeabi-v7a": 3}


def _lname(tag: str) -> str:
    """Имя тега без XML-неймспейса."""
    return tag.rsplit("}", 1)[-1]


def _child(elem: ET.Element, name: str) -> ET.Element | None:
    for ch in elem:
        if _lname(ch.tag) == name:
            return ch
    return None


def _ctext(elem: ET.Element | None, name: str, default: str = "") -> str:
    if elem is None:
        return default
    ch = _child(elem, name)
    return (ch.text or "").strip() if ch is not None else default


def fetch_bytes(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "PixEmuStudio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:  # noqa: BLE001 — покажем пользователю как есть
        raise AppError(f"Не удалось скачать манифест:\n{url}\n\n{exc}") from exc


def _abs_url(manifest_url: str, rel: str) -> str:
    if rel.startswith("http"):
        return rel
    return urllib.parse.urljoin(manifest_url, rel)


def _parse_api(raw: str) -> tuple[int, float] | None:
    """api-level бывает '35' и '37.0' (расширенные SDK). → (int, float)."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        f = float(raw)
    except ValueError:
        return None  # встречаются кодовые имена вроде 'UpsideDownCake'
    return (int(f), f)


def _rev_tuple(rev_el: ET.Element | None) -> tuple[int, ...]:
    if rev_el is None:
        return (0,)
    parts = []
    for name in ("major", "minor", "micro"):
        try:
            parts.append(int(_ctext(rev_el, name, "0") or 0))
        except ValueError:
            parts.append(0)
    if not any(parts) and (rev_el.text or "").strip().isdigit():
        parts[0] = int(rev_el.text.strip())
    return tuple(parts)


def parse_system_images(xml_text: str, manifest_url: str, source: str) -> list[ImageSpec]:
    """Разбор sys-img2-3.xml → список образов (новые API первыми)."""
    root = ET.fromstring(xml_text)
    out: list[ImageSpec] = []
    for pkg in root.iter():
        if _lname(pkg.tag) != "remotePackage":
            continue
        details = _child(pkg, "type-details")
        if details is None:
            continue
        api_raw = _ctext(details, "api-level")
        parsed = _parse_api(api_raw)
        if parsed is None:
            continue
        api_int, api_float = parsed
        abi = _ctext(details, "abi", "x86_64") or "x86_64"
        rev_t = _rev_tuple(_child(pkg, "revision"))
        rev = ".".join(str(x) for x in rev_t)
        archives = _child(pkg, "archives")
        if archives is None:
            continue
        for arch in archives:
            if _lname(arch.tag) != "archive":
                continue
            complete = _child(arch, "complete")
            if complete is None:
                continue
            url = _ctext(complete, "url")
            if not url:
                continue
            sha1 = ""
            for cs in complete:
                if _lname(cs.tag) == "checksum" and cs.attrib.get("type", "sha1") == "sha1":
                    sha1 = (cs.text or "").strip()
            out.append(ImageSpec(
                api=api_int, source=source, abi=abi,
                url=_abs_url(manifest_url, url), sha1=sha1,
                size=int(_ctext(complete, "size", "0") or 0),
                revision=rev, api_str=api_raw.strip(),
            ))
    # Дедупликация: один (API, ABI) может встречаться с несколькими ревизиями
    # (расширенные SDK) — оставляем новейшую.
    def rev_i(img: ImageSpec) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in img.revision.split("."))
        except ValueError:
            return (0,)

    best: dict[tuple[str, str], ImageSpec] = {}
    for img in out:
        key = (img.api_str, img.abi)
        if key not in best or rev_i(img) > rev_i(best[key]):
            best[key] = img
    out = list(best.values())

    pref = _ABI_PREFERENCE.get
    out.sort(key=lambda i: pref(i.abi, 9))
    out.sort(key=lambda i: (_parse_api(i.api_str) or (0, 0.0))[1], reverse=True)
    return out


def load_channel(source: str, cache_dir: Path, refresh: bool = False) -> list[ImageSpec]:
    """Список образов источника с кэшированием XML на диске."""
    if source not in CHANNELS:
        raise AppError(f"Неизвестный источник образов: {source}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"sysimg_{source}.xml"
    if refresh or not cache.exists():
        cache.write_bytes(fetch_bytes(CHANNELS[source][1]))
    images = parse_system_images(cache.read_text(encoding="utf-8",
                                                 errors="replace"),
                                 CHANNELS[source][1], source)
    if not images:
        raise AppError("Манифест загружен, но образов не найдено. "
                       "Попробуйте «Обновить список».")
    return images


def parse_emulator_package(xml_text: str) -> dict:
    """Из repository2-3.xml выбираем свежий СТАБИЛЬНЫЙ (channel-0) эмулятор
    для Windows x64. Preview/canary-каналы игнорируются."""
    root = ET.fromstring(xml_text)
    best: dict | None = None
    best_rank: tuple[int, tuple[int, ...]] | None = None
    for pkg in root.iter():
        if _lname(pkg.tag) != "remotePackage" or pkg.attrib.get("path") != "emulator":
            continue
        rev = _rev_tuple(_child(pkg, "revision"))
        channel = "channel-0"
        for ch in pkg:
            if _lname(ch.tag) == "channelRef":
                channel = ch.attrib.get("ref", channel)
        # channel-0 = stable. Остальные каналы берём только если стабильного нет.
        rank = 0 if channel == "channel-0" else 1
        archives = _child(pkg, "archives")
        if archives is None:
            continue
        for arch in archives:
            if _lname(arch.tag) != "archive":
                continue
            if _ctext(arch, "host-os") != "windows":
                continue
            arch_name = _ctext(arch, "host-arch", "x64") or "x64"
            if arch_name != "x64":
                continue
            complete = _child(arch, "complete")
            if complete is None:
                continue
            url = _ctext(complete, "url")
            if not url:
                continue
            key = (rank, tuple(-x for x in rev))
            if best_rank is not None and key > best_rank:
                continue
            sha1 = ""
            for cs in complete:
                if _lname(cs.tag) == "checksum":
                    sha1 = (cs.text or "").strip()
            best_rank = key
            best = {
                "url": _abs_url(EMULATOR_MANIFEST_URL, url),
                "sha1": sha1,
                "size": int(_ctext(complete, "size", "0") or 0),
                "version": ".".join(map(str, rev)),
                "channel": channel,
            }
    if not best:
        raise AppError("В манифесте Google не найден эмулятор для Windows.")
    return best


def load_emulator_package(cache_dir: Path, refresh: bool = False) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / "emulator_repo.xml"
    if refresh or not cache.exists():
        cache.write_bytes(fetch_bytes(EMULATOR_MANIFEST_URL))
    return parse_emulator_package(cache.read_text(encoding="utf-8", errors="replace"))
