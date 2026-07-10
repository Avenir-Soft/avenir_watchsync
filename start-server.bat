@echo off
title Watch Sync
echo Starting Watch Sync in Docker...
wsl -d Ubuntu -- bash -lc "cd /home/shokh/watch-sync && docker compose up -d"
echo.
echo   Watch Sync is running:
echo     PC:    http://localhost:8765
echo     Phone: http://192.168.1.8:8765
echo.
echo   To stop: use Docker Desktop, or: docker compose down
echo.
timeout /t 6 >nul
