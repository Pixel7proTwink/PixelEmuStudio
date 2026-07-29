#!/usr/bin/env python3
"""Точка входа приложения (используется PyInstaller и для простого запуска
`python pixemu_studio.py` без установки пакета)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher.app import main  # noqa: E402

if __name__ == "__main__":
    main()
