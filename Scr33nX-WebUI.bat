@echo off
rem Scr33nX — new web-based UI (redesign preview). No console window.
rem The classic UI remains available via Scr33nX.bat until final cutover.
start "" pythonw "%~dp0src\app_web.py"
