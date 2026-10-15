@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo ========================================================
echo   DONG GOI EXE: AUTO RANDOM VIDEO REMIX
echo ========================================================
echo.

set PY_BIN=e:\TOOL\python\python.exe
if not exist "%PY_BIN%" (
    if exist "%~dp0..\python\python.exe" (
        set "PY_BIN=%~dp0..\python\python.exe"
    ) else (
        set PY_BIN=python
    )
)

echo Dang su dung Python: %PY_BIN%
echo Dang kiem tra PyInstaller...
"%PY_BIN%" -m pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Dang cai dat PyInstaller...
    "%PY_BIN%" -m pip install pyinstaller
)

echo.
echo Dang dong goi ung dung bang PyInstaller...
echo Vui long doi trong giay lat...
echo.

"%PY_BIN%" -m PyInstaller ^
    --name "AutoRandomVideoRemix" ^
    --windowed ^
    --noconfirm ^
    --clean ^
    --add-data "history;history" ^
    --collect-all qfluentwidgets ^
    app.py

if %errorlevel% equ 0 (
    echo.
    echo ========================================================
    echo   [THANH CONG] DA TAO FILE EXE HOAN CHINH!
    echo   Duong dan file chay:
    echo   %~dp0dist\AutoRandomVideoRemix\AutoRandomVideoRemix.exe
    echo ========================================================
) else (
    echo.
    echo [LOI] Dong goi that bai. Ma loi: %errorlevel%
)

pause
