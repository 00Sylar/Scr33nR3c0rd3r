@echo off
cd /d "%~dp0"
rem pythonw = console-less Python: only the GUI window appears.
start "" pythonw app.py
exit /b
