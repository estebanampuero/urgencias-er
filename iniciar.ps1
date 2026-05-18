# ============================================================
#  Iniciar Sistema de Entrega de Turno - Urgencias (PowerShell)
# ============================================================
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$PY   = Join-Path $ROOT "python\python.exe"

if (-not (Test-Path $PY)) {
  Write-Host "[ERROR] Python portable no encontrado." -ForegroundColor Red
  Write-Host "Ejecuta primero:  .\setup.ps1" -ForegroundColor Yellow
  Read-Host "Enter para salir"
  exit 1
}

$Host.UI.RawUI.WindowTitle = "Urgencias - Servidor Entrega de Turno"
Push-Location $ROOT
try {
  & $PY "app.py"
} finally {
  Pop-Location
  Write-Host ""
  Write-Host "El servidor se detuvo." -ForegroundColor Yellow
  Read-Host "Enter para salir"
}
