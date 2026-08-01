@echo off
setlocal
cd /d "%~dp0"
if not exist "Dependencies\HidHide_1.4.202_x64.exe" (
    echo ERROR: HidHide installer was not found.
    pause
    exit /b 1
)
echo Starting the official HidHide installer...
start "" /wait "Dependencies\HidHide_1.4.202_x64.exe"
echo.
echo HidHide installer has finished.
echo Open HidHide Configuration Client manually if you need to hide a physical controller.
pause
