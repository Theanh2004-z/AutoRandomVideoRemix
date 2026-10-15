@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo ========================================================
echo   KHOI DONG: AUTO RANDOM VIDEO REMIX
echo ========================================================
echo Dang khoi dong ung dung...

if exist "e:\TOOL\python\python.exe" (
    "e:\TOOL\python\python.exe" "app.py"
    goto finish
)

if exist "%~dp0..\python\python.exe" (
    "%~dp0..\python\python.exe" "app.py"
    goto finish
)

python --version >nul 2>&1
if %errorlevel% equ 0 (
    python "app.py"
    goto finish
)

echo [LOI] Khong tim thay Python!
echo Vui long kiem tra lai duong dan e:\TOOL\python\python.exe
pause
exit /b 1

:finish
if %errorlevel% neq 0 (
    echo.
    echo [THONG BAO] Ung dung da dong voi ma loi %errorlevel%.
    pause
)
