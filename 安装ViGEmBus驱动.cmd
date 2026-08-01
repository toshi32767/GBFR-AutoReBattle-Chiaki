@echo off
setlocal
cd /d "%~dp0"
if not exist "Dependencies\ViGEmBus_1.22.0_x64_x86_arm64.exe" (
    echo Missing official ViGEmBus driver installer.
    pause
    exit /b 1
)
echo Starting the official ViGEmBus driver installer...
start "" /wait "Dependencies\ViGEmBus_1.22.0_x64_x86_arm64.exe"
echo.
echo Driver installation has finished. Restart Windows if the driver installer requests it.
pause
