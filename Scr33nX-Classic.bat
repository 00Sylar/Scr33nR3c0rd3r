@echo off
rem Scr33nX — classic Tk interface (same engine, same settings, same config
rem files as the default web UI). Double-click convenience for:
rem   Scr33nX.bat --classic
cd /d "%~dp0src"
start "" pythonw app.py
exit /b
