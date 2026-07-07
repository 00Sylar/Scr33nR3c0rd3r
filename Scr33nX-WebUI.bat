@echo off
rem Scr33nX — web UI (this is now the default; identical to double-clicking
rem Scr33nX.bat with no arguments). Kept as an alias so any shortcut made
rem to this file during the redesign preview keeps working.
cd /d "%~dp0src"
start "" pythonw app_web.py
exit /b
