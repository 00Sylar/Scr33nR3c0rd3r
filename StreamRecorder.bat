@echo off
title WebcamRecorder v2 (TEST) - Stripchat+Playwright
cd /d "%~dp0"
python app.py
if errorlevel 1 (
    echo.
    echo ERROR: Something went wrong. Make sure Python is installed.
    pause
)
