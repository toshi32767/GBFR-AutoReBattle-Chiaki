@echo off
setlocal
cd /d "%~dp0"
GBFR_AutoReBattle\GBFR_AutoReBattle.exe --account-id
if errorlevel 1 pause
