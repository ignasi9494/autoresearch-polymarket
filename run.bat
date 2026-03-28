@echo off
title AutoResearch Polymarket
echo ============================================================
echo   AUTORESEARCH POLYMARKET - Binary Arbitrage
echo   Karpathy-style autonomous research loop
echo ============================================================
echo.

cd /d "%~dp0"

echo [1/3] Inicializando base de datos...
python db.py
if errorlevel 1 (
    echo ERROR: No se pudo inicializar la DB
    pause
    exit /b 1
)

echo [2/3] Arrancando dashboard en http://localhost:8080 ...
start "Dashboard Server" /min python server.py
timeout /t 2 /nobreak >nul
start http://localhost:8080

echo [3/3] Arrancando orchestrator (bucle autonomo)...
echo.
echo   Ctrl+C para parar el orchestrator.
echo   El dashboard seguira corriendo en segundo plano.
echo   Para parar el dashboard, cierra la ventana "Dashboard Server".
echo.
echo ============================================================
echo.

python orchestrator.py

echo.
echo Orchestrator detenido.
pause
