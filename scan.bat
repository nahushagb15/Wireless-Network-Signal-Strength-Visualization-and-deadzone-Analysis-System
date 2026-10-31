@echo off
setlocal
cd /d "%~dp0"
title Deadzone Mapper - scanner
call .venv\Scripts\activate
echo Starting the scanner for your newest active session.
echo Tap a spot on the floor plan, wait for 3 lines here, then walk to the next spot.
echo Press Ctrl+C to stop.
echo.
python -m scanner.scanner --mode checkpoint
pause
