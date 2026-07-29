@echo off
rem PixelEmu Studio launcher (Python 3.13).
rem Messages are ASCII-only on purpose: cmd.exe reads .bat in OEM code page.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3.13 -m launcher %*
) else (
    python -m launcher %*
)
pause
