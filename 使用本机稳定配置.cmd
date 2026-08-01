@echo off
setlocal
cd /d "%~dp0"
rem Known-good Chiaki keyboard mapping:
rem W=Left Stick Up, Return=Cross, \=Square, L=L2, 3=R1, T=Touchpad.
rem Keep foreground mode as the baseline; enable background mode from the GUI
rem only after checking the virtual DS4 report.
GBFR.exe --gui --window-title "Chiaki | Stream" --l2-key l --refocus-seconds 10
if errorlevel 1 pause
