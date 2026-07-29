"""Запуск:  python -m launcher"""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    raise SystemExit("Нужен Python 3.11+ (рекомендуется 3.13.12). "
                     f"Сейчас: {sys.version}")

from .app import main

if __name__ == "__main__":
    main()
