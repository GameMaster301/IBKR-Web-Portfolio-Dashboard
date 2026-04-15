@echo off
title IBKR Dashboard — Update
cd /d "%~dp0"

echo.
echo  Updating IBKR Portfolio Dashboard to the latest version...
echo  (This may take a minute on slow connections)
echo.

docker compose pull
docker compose up -d

echo.
echo  Update complete. Dashboard restarted at http://localhost:8050
echo.
pause
