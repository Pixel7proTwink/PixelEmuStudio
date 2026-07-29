"""Автопроверка парсеров манифестов Google на мини-фикстурах (без сети).
Запуск:  python tests/test_repo.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from launcher.repo import parse_emulator_package, parse_system_images  # noqa: E402

# Формат соответствует настоящему sys-img2-3.xml от Google (проверен 07.2026).
SYSIMG_XML = """<?xml version="1.0"?>
<sdk:sdk-sys-img xmlns:sdk="http://schemas.android.com/sdk/android/repo/sys-img2/01">
  <license id="android-sdk-license" type="text">Terms</license>
  <remotePackage path="system-images;android-35;google_apis_playstore;x86_64">
    <type-details xsi:type="sys-img:sysImgDetailsType" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <api-level>35</api-level>
      <tag><id>google_apis_playstore</id><display>Google Play</display></tag>
      <vendor><id>google</id><display>Google Inc.</display></vendor>
      <abi>x86_64</abi>
    </type-details>
    <revision><major>9</major></revision>
    <display-name>Google Play Intel x86_64 Atom System Image</display-name>
    <uses-license ref="android-sdk-license"/>
    <channelRef ref="channel-0"/>
    <archives>
      <archive>
        <complete>
          <size>2888906752</size>
          <checksum type="sha1">deadbeefcafebabe1234567890abcdef12345678</checksum>
          <url>x86_64-35_r09.zip</url>
        </complete>
      </archive>
    </archives>
  </remotePackage>
  <remotePackage path="system-images;android-37.0;google_apis_playstore;arm64-v8a">
    <type-details xsi:type="sys-img:sysImgDetailsType" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <api-level>37.0</api-level>
      <extension-level>22</extension-level>
      <base-extension>true</base-extension>
      <tag><id>google_apis_playstore</id></tag>
      <abi>arm64-v8a</abi>
    </type-details>
    <revision><major>6</major></revision>
    <channelRef ref="channel-0"/>
    <archives>
      <archive>
        <complete>
          <size>2257751510</size>
          <checksum type="sha1">aaaabbbbccccddddeeeeffff0000111122223333</checksum>
          <url>arm64-v8a-37.0_r06.zip</url>
        </complete>
      </archive>
    </archives>
  </remotePackage>
</sdk:sdk-sys-img>
"""

REPO_XML = """<?xml version="1.0"?>
<sdk:sdk-repository xmlns:sdk="http://schemas.android.com/sdk/android/repo/repository2/01">
  <remotePackage path="emulator">
    <revision><major>35</major><minor>4</minor><micro>9</micro></revision>
    <archives>
      <archive>
        <complete>
          <size>470000000</size>
          <checksum type="sha1">1111222233334444555566667777888899990000</checksum>
          <url>emulator-windows_x64-13087342.zip</url>
        </complete>
        <host-os>windows</host-os>
      </archive>
      <archive>
        <complete>
          <size>460000000</size>
          <checksum type="sha1">0000111122223333444455556666777788889999</checksum>
          <url>emulator-linux_x64-13087342.zip</url>
        </complete>
        <host-os>linux</host-os>
      </archive>
    </archives>
  </remotePackage>
  <remotePackage path="emulator">
    <revision><major>99</major><minor>0</minor><micro>0</micro></revision>
    <channelRef ref="channel-2"/>
    <archives>
      <archive>
        <complete>
          <size>500000000</size>
          <checksum type="sha1">abcdefabcdefabcdefabcdefabcdefabcdefabcd</checksum>
          <url>emulator-windows_x64-99999999.zip</url>
        </complete>
        <host-os>windows</host-os>
        <host-arch>x64</host-arch>
      </archive>
    </archives>
  </remotePackage>
</sdk:sdk-repository>
"""


def main() -> None:
    imgs = parse_system_images(SYSIMG_XML,
                               "https://dl.google.com/android/repository/"
                               "sys-img/google_apis_playstore/sys-img2-3.xml",
                               "play")
    assert len(imgs) == 2, imgs
    # сортировка: сначала новый API (37.0 → первым)
    assert imgs[0].api_str == "37.0" and imgs[0].abi == "arm64-v8a", imgs[0]
    assert imgs[0].api == 37, imgs[0]
    assert imgs[1].api_str == "35" and imgs[1].abi == "x86_64", imgs[1]
    assert imgs[1].url.endswith("/sys-img/google_apis_playstore/x86_64-35_r09.zip"), imgs[1].url
    assert imgs[1].sha1 == "deadbeefcafebabe1234567890abcdef12345678"
    assert imgs[1].size == 2888906752
    print("parse_system_images: OK")

    pkg = parse_emulator_package(REPO_XML)
    # стабильный канал должен побеждать preview, даже с большей версией
    assert pkg["version"] == "35.4.9", pkg
    assert pkg["channel"] == "channel-0", pkg
    assert "windows" in pkg["url"], pkg
    assert pkg["url"].startswith("https://dl.google.com/android/repository/"), pkg
    assert pkg["sha1"] == "1111222233334444555566667777888899990000"
    print("parse_emulator_package: OK")

    print("Все тесты пройдены.")


if __name__ == "__main__":
    main()
