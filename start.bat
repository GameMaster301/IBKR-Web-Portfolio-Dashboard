@echo off
title IBKR Portfolio Dashboard
cd /d "%~dp0"

echo.
echo  Starting IBKR Portfolio Dashboard...
echo.

docker compose up -d
if errorlevel 1 (
    echo.
    echo  ERROR: Could not start the dashboard.
    echo  Make sure Docker Desktop is running ^(whale icon in your taskbar^).
    echo.
    pause
    exit /b 1
)

timeout /t 5 /nobreak >nul
start http://localhost:8050

echo  Dashboard is running at http://localhost:8050
echo.
echo  This window can be closed — the dashboard keeps running in the background.
echo  To stop it, run stop.bat
echo.
pause
