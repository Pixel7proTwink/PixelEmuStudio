# Как поучаствовать

Спасибо за интерес к PixelEmu Studio! 🙂

## Перед PR

1. Убедитесь, что проходят проверки:
   ```powershell
   python -m compileall -q launcher tests pixemu_studio.py
   python tests\test_repo.py
   python tests\test_install.py
   ```
2. C++-ядро собирается без предупреждений (`native\Makefile`, MSYS2 UCRT64).
3. Новые баги/фичи — с тестом в `tests/` (фикстуры без сети; «живые» проверки
   манифестов Google держим отдельно и не делаем обязательными).

## Правила кода

- Python: только стандартная библиотека (GUI-пользователю не нужен `pip`).
- C++: C++17, без внешних зависимостей — WinAPI (WinHTTP, BCrypt, CreateProcess).
- Сообщения UI — на русском, комментарии в коде можно RU/EN.
- Любые пути в `avd/config.ini` — ТОЛЬКО в формате avdmanager
  (одинарные слеши, `image.sysdir.1` относительный; см. историю багов 1.0.x —
  парсер ini у эмулятора не понимает экранирование).

## Что было бы полезно

- Установка APK из GUI (через platform-tools/adb).
- Скины устройств с рамкой (assets/skins).
- Клонирование и экспорт/импорт AVD.
- Профили ARM-образов для Windows on ARM.
