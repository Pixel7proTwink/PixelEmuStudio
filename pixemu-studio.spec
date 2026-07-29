# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: сборка PixelEmu Studio в один PixelEmuStudio.exe.

Локально:  pyinstaller --noconfirm pixemu-studio.spec
C++-ядро native/bin/pixemu-core.exe попадает ВНУТРЬ exe (извлекается в
_MEIPASS при запуске), поэтому релиз — один файл.
"""
import os
from pathlib import Path

ROOT = Path.cwd()

datas = [
    ("README.md", "."),
    ("LICENSE", "."),
    ("assets/icon.png", "assets"),
]
datas = [(src, dst) for src, dst in datas if (ROOT / src).exists()]

binaries = []
core = ROOT / "native" / "bin" / "pixemu-core.exe"
if core.exists():
    binaries.append((str(core), "native/bin"))
else:
    print("ВНИМАНИЕ: pixemu-core.exe не найден — в сборке не будет C++-ядра "
          "(загрузки пойдут через Python-загрузчик).")

a = Analysis(
    ["pixemu_studio.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog",
                   "tkinter.messagebox"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["test", "unittest", "pydoc"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PixelEmuStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                      # GUI-приложение, без консоли
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.ico")
    if (ROOT / "assets" / "icon.ico").exists() else None,
)
