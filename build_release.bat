@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set REMIX_NO_PAUSE=1
set "PY_BIN=E:\TOOL\python\python.exe"
if not exist "%PY_BIN%" set "PY_BIN=python"
call build_exe.bat
if errorlevel 1 goto failed
"%PY_BIN%" scripts\build_release.py --installer
if errorlevel 1 goto failed
echo.
echo [THANH CONG] Setup.exe, ZIP va update_manifest.json nam trong thu muc releases.
echo Gui ban Setup.exe cho nguoi dung moi. Tai ca ZIP va manifest len GitHub de tu dong cap nhat.
pause
exit /b 0
:failed
echo [LOI] Chua tao duoc ban phat hanh hoan chinh. Kiem tra thong bao phia tren.
pause
exit /b 1
