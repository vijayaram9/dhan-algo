@echo off
title Dhan Nifty Algo Trader
cd /d "%~dp0"
echo ========================================================
echo       Dhan Nifty Intraday Algorithmic Trading Bot
echo ========================================================
echo.
.venv\Scripts\python.exe main.py --mode paper --ticks 0 --interval 3
pause
