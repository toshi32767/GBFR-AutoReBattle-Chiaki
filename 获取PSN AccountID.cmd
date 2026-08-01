@echo off
setlocal
cd /d "%~dp0"
set "APP=%~dp0GBFR_AutoReBattle.exe"
if not exist "%APP%" set "APP=%~dp0GBFR.exe"
if not exist "%APP%" set "APP=%~dp0GBFR_AutoReBattle\GBFR_AutoReBattle.exe"
if not exist "%APP%" (
    echo 找不到 GBFR_AutoReBattle.exe，请确认已完整解压当前文件夹。
    pause
    exit /b 1
)
"%APP%" --account-id
if errorlevel 1 pause
