@echo off
setlocal
cd /d "%~dp0"
title Deadzone Mapper - server

if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: installing packages. This takes a few minutes...
  python -m venv .venv
  if errorlevel 1 (
    echo.
    echo Python was not found. Install it from python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
  )
  call .venv\Scripts\activate
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Package install failed. Check your internet connection and try again.
    pause
    exit /b 1
  )
) else (
  call .venv\Scripts\activate
)

echo.
echo Opening http://localhost:5000  (keep this window open while you work)
start "" http://localhost:5000
python run.py
pause
