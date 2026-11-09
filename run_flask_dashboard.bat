@echo off
title Dhan Nifty Algo Trader - Web Dashboard (Flask)
cd /d "%~dp0"
echo ========================================================
echo   Launching Dhan Nifty Trading Web Hub (Flask)...
echo   Open your browser to: http://localhost:5000
echo ========================================================
echo.
start http://localhost:5000
.venv\Scripts\python.exe app.py
pause
