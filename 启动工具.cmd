@echo off
setlocal
cd /d "%~dp0"
GBFR.exe --gui %*
if errorlevel 1 pause
