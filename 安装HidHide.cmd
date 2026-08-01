@echo off
setlocal
cd /d "%~dp0"
if not exist "Dependencies\HidHide_1.4.202_x64.exe" (
    echo 找不到官方 HidHide 安装程序。
    pause
    exit /b 1
)
echo 正在启动官方 HidHide 安装程序...
start "" /wait "Dependencies\HidHide_1.4.202_x64.exe"
echo.
echo HidHide 安装程序已结束。
echo 如需隔离实体手柄，请手动打开 HidHide Configuration Client 配置隐藏设备。
pause
