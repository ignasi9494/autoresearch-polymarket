@echo off
title AutoResearch - Dashboard Only
echo ============================================================
echo   AUTORESEARCH POLYMARKET - Dashboard
echo   http://localhost:8080
echo ============================================================
echo.

cd /d "%~dp0"

python db.py
timeout /t 1 /nobreak >nul
start http://localhost:8080

echo Dashboard corriendo. Ctrl+C para parar.
echo.

python server.py
