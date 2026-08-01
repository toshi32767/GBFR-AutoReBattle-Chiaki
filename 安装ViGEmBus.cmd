@echo off
setlocal
cd /d "%~dp0"
if not exist "Dependencies\ViGEmBus_1.22.0_x64_x86_arm64.exe" (
    echo 找不到官方 ViGEmBus 驱动安装程序。
    pause
    exit /b 1
)
echo 正在启动官方 ViGEmBus 驱动安装程序...
start "" /wait "Dependencies\ViGEmBus_1.22.0_x64_x86_arm64.exe"
echo.
echo ViGEmBus 安装程序已结束。如果提示重启 Windows，请先重启。
pause
