@echo off
setlocal
cd /d "%~dp0"
if not exist "Dependencies\ViGEmBus_1.22.0_x64_x86_arm64.exe" (
    echo ERROR: ViGEmBus installer was not found.
    pause
    exit /b 1
)
echo Starting the official ViGEmBus driver installer...
start "" /wait "Dependencies\ViGEmBus_1.22.0_x64_x86_arm64.exe"
echo.
echo ViGEmBus installer has finished. Restart Windows if requested.
pause
