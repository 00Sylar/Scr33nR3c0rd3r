@echo off
rem Source lives in src\; cd there so app.py's/app_web.py's __file__-relative
rem paths resolve. pythonw = console-less Python: only the GUI window appears.
cd /d "%~dp0src"
if /I "%~1"=="--classic" (
    start "" pythonw app.py
) else (
    start "" pythonw app_web.py
)
exit /b
