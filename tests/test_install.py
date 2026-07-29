"""Регрессионные тесты на исправленные баги версии 1.0:
  1) архивы Google содержат вложенную папку (x86_64/system.img) — распаковка
     должна «срезать» её (strip_root=True);
  2) старые распаковки вида sysdir/abi/abi/system.img самолечатся без перекачивания;
  3) кириллические имена AVD транслитерируются в ASCII.
Запуск:  python tests/test_install.py
"""
import sys
import tempfile
import zipfile
from pathlib import Path

# Windows-консоль может быть cp1251 — переводим вывод тестов в UTF-8,
# иначе кириллица/стрелки в print падают с UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from launcher.config import EmuConfig, ImageSpec  # noqa: E402
from launcher.netio import _common_root_prefix, unzip  # noqa: E402
from launcher.util import sanitize_avd_name    # noqa: E402
from launcher import avd as avd_mod            # noqa: E402


def make_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def test_strip_root() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        zp = td / "img.zip"
        make_zip(zp, {
            "x86_64/system.img": b"SYS",
            "x86_64/ramdisk.img": b"RAM",
            "x86_64/data/misc/x.txt": b"X",
            "x86_64/": b"",
        })
        dest = td / "out"
        unzip(zp, dest, strip_root=True)
        assert (dest / "system.img").read_bytes() == b"SYS"
        assert (dest / "ramdisk.img").read_bytes() == b"RAM"
        assert (dest / "data" / "misc" / "x.txt").read_bytes() == b"X"
        assert not (dest / "x86_64").exists(), "вложенную папку не срезали"

        dest2 = td / "out2"     # без strip_root — старое поведение
        unzip(zp, dest2, strip_root=False)
        assert (dest2 / "x86_64" / "system.img").exists()
    print("unzip(strip_root): OK")


def test_common_root() -> None:
    assert _common_root_prefix(["a/b", "a/c/d"]) == "a/"
    assert _common_root_prefix(["file.txt", "a/b"]) is None   # файл в корне
    assert _common_root_prefix(["a/b", "c/d"]) is None        # две папки
    assert _common_root_prefix([]) is None
    print("_common_root_prefix: OK")


def test_repair_layout() -> None:
    with tempfile.TemporaryDirectory() as td:
        sdk = Path(td)
        img = ImageSpec(api=35, api_str="35", abi="x86_64", url="https://x/y.zip")
        broken = img.sysdir(sdk) / "x86_64"     # как распаковывала v1.0
        broken.mkdir(parents=True)
        (broken / "system.img").write_bytes(b"SYS")
        (broken / "ramdisk.img").write_bytes(b"RAM")
        assert img.is_downloaded(sdk), "самолечение не сработало"
        assert (img.sysdir(sdk) / "system.img").read_bytes() == b"SYS"
        print("repair_layout (самолечение): OK")


def test_translit() -> None:
    assert sanitize_avd_name("Мой Пиксель") == "Moy_Piksel"
    assert sanitize_avd_name("Pixel 7 Pro!") == "Pixel_7_Pro"
    assert sanitize_avd_name("###") == "PixelAVD"
    print(f"sanitize_avd_name: OK ('Мой Пиксель' → '{sanitize_avd_name('Мой Пиксель')}')")


def test_avd_config_keys() -> None:
    """Баг 1.0.1«FATAL CPU Architecture 'arm'»: эмулятор падает, если в
    config.ini нет hw.cpu.arch. Проверяем полный набор ключей avdmanager."""
    with tempfile.TemporaryDirectory() as td:
        sdk = Path(td)
        (sdk / "avds").mkdir(parents=True)
        cfg = EmuConfig(avd_name="Pixel7Pro_API35",
                        image=ImageSpec(api=35, api_str="35", abi="x86_64"))
        d = avd_mod.ensure_avd(cfg, sdk)
        ini = (d / "config.ini").read_text(encoding="utf-8")
        for must in ("hw.cpu.arch=x86_64",
                     "abi.type=x86_64",
                     "hw.lcd.width=1440",
                     "hw.ramSize=8192",
                     "hw.cpu.ncore=4",
                     "disk.dataPartition.size=17179869184",   # 16 ГБ байтами
                     "hw.gpu.mode=auto",
                     "tag.id=google_apis_playstore",
                     # баг 1.0.2 «Broken AVD system path»: sysdir только
                     # относительный, с одинарными слешами, как у avdmanager
                     "image.sysdir.1=system-images\\android-35\\"
                     "google_apis_playstore\\x86_64\\"):
            assert must in ini, f"нет строки: {must}"
        assert "\\\\" not in ini, "двойные слеши — экранирование сломано"
        assert "\\:" not in ini, "экранированное двоеточие — сломано"
        # баг 1.0.3 «unknown skin name '_no_skin'»: только динамический WxH
        assert "skin.name=1440x3120" in ini
        assert "_no_skin" not in ini
        # arm64-образ → hw.cpu.arch=arm64
        cfg_arm = EmuConfig(avd_name="ArmTest",
                            image=ImageSpec(api=34, api_str="34", abi="arm64-v8a"))
        ini_arm = (avd_mod.ensure_avd(cfg_arm, sdk) / "config.ini") \
            .read_text(encoding="utf-8")
        assert "hw.cpu.arch=arm64" in ini_arm
    print("ensure_avd (hw.cpu.arch и прочие ключи): OK")


if __name__ == "__main__":
    test_strip_root()
    test_common_root()
    test_repair_layout()
    test_translit()
    test_avd_config_keys()
    print("Все тесты пройдены.")
