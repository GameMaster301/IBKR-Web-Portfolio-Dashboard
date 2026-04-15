@echo off
title IBKR Dashboard — Stop
cd /d "%~dp0"

echo.
echo  Stopping IBKR Portfolio Dashboard...
docker compose down
echo  Done.
echo.
pause
