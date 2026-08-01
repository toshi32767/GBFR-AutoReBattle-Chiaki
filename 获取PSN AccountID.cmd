@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0GBFR_AutoReBattle.exe" goto current
if exist "%~dp0GBFR.exe" goto legacy
if exist "%~dp0GBFR_AutoReBattle\GBFR_AutoReBattle.exe" goto nested
echo ERROR: GBFR_AutoReBattle.exe was not found.
echo Extract the complete package and run this file again.
pause
exit /b 1
:current
"%~dp0GBFR_AutoReBattle.exe" --account-id
goto finish
:legacy
"%~dp0GBFR.exe" --account-id
goto finish
:nested
"%~dp0GBFR_AutoReBattle\GBFR_AutoReBattle.exe" --account-id
:finish
if errorlevel 1 pause
