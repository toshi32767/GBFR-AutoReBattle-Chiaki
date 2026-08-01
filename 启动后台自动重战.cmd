@echo off
setlocal
cd /d "%~dp0"
GBFR_AutoReBattle\GBFR_AutoReBattle.exe --start-chiaki --background %*
if errorlevel 1 pause
