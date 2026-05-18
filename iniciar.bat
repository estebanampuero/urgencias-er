@echo off
REM ============================================================
REM  Iniciar Sistema de Entrega de Turno - Urgencias
REM  Doble click para arrancar el servidor.
REM ============================================================
cd /d "%~dp0"

if not exist "python\python.exe" (
  echo.
  echo [ERROR] Python portable no encontrado.
  echo Ejecuta primero: setup.ps1
  echo.
  pause
  exit /b 1
)

title Urgencias - Servidor Entrega de Turno
echo ================================================================
echo   Iniciando servidor...
echo   Para detener: cierra esta ventana o presiona Ctrl+C
echo ================================================================

REM Producción usa waitress (multi-thread). Fallback a app.py si no está.
"python\python.exe" serve.py

echo.
echo El servidor se detuvo.
pause
