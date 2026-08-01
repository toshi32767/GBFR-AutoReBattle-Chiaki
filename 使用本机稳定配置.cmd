@echo off
setlocal
cd /d "%~dp0"
rem Known-good Chiaki keyboard mapping:
rem W/S/A/D=Left Stick, Q/E=Right Stick Left/Right,
rem Return=Cross, \=Square, L=L2, 3=R1. Touchpad is not used.
rem Keep foreground mode as the baseline; enable background mode from the GUI
rem only after checking the virtual DS4 report.
set "APP=%~dp0GBFR_AutoReBattle.exe"
if not exist "%APP%" set "APP=%~dp0GBFR.exe"
if not exist "%APP%" set "APP=%~dp0GBFR_AutoReBattle\GBFR_AutoReBattle.exe"
if not exist "%APP%" (
    echo 找不到 GBFR_AutoReBattle.exe，请确认已完整解压当前文件夹。
    pause
    exit /b 1
)
"%APP%" --gui --window-title "Chiaki | Stream" --l2-key l --refocus-seconds 15 %*
if errorlevel 1 pause
