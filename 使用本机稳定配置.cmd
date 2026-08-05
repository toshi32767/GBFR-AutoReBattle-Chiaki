@echo off
setlocal
cd /d "%~dp0"
rem The GUI synchronizes the current Chiaki keyboard mapping automatically.
rem Keep foreground mode as the baseline; enable background mode from the GUI
rem after the environment check if other windows must cover Chiaki.
if exist "%~dp0GBFR_AutoReBattle.exe" goto current
if exist "%~dp0GBFR.exe" goto legacy
if exist "%~dp0GBFR_AutoReBattle\GBFR_AutoReBattle.exe" goto nested
echo ERROR: GBFR_AutoReBattle.exe was not found.
echo Extract the complete package and run this file again.
pause
exit /b 1
:current
"%~dp0GBFR_AutoReBattle.exe" --gui --window-title "Chiaki | Stream" --l2-key l --refocus-seconds 15 %*
goto finish
:legacy
"%~dp0GBFR.exe" --gui --window-title "Chiaki | Stream" --l2-key l --refocus-seconds 15 %*
goto finish
:nested
"%~dp0GBFR_AutoReBattle\GBFR_AutoReBattle.exe" --gui --window-title "Chiaki | Stream" --l2-key l --refocus-seconds 15 %*
:finish
if errorlevel 1 pause
