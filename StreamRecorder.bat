@echo off
rem Source lives in src\; cd there so app.py's __file__-relative paths resolve.
cd /d "%~dp0src"
rem pythonw = console-less Python: only the GUI window appears.
start "" pythonw app.py
exit /b
