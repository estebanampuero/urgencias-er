# =====================================================================
# setup.ps1 — Instalación de Python portable + dependencias.
# Ejecutar UNA VEZ en el PC servidor del hospital, sin permisos de admin.
# Uso:  Click derecho -> "Ejecutar con PowerShell"  ó:
#       powershell -ExecutionPolicy Bypass -File .\setup.ps1
# =====================================================================
$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

# Versión de Python embeddable (sin instalador, sin admin)
$PY_VER  = "3.11.9"
$PY_ARCH = "amd64"   # cambia a "win32" si el PC es de 32 bits
$ROOT    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PY_DIR  = Join-Path $ROOT "python"
$PY_EXE  = Join-Path $PY_DIR "python.exe"

Write-Host "================================================================"
Write-Host "  Setup · Sistema de Entrega de Turno (Urgencias)"
Write-Host "================================================================"
Write-Host "  Carpeta destino: $ROOT"
Write-Host ""

# 1) Descargar Python embeddable si no existe
if (-not (Test-Path $PY_EXE)) {
  Write-Host "[1/4] Descargando Python $PY_VER embeddable..."
  $url = "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-embed-$PY_ARCH.zip"
  $zip = Join-Path $env:TEMP "python-embed.zip"
  Invoke-WebRequest -Uri $url -OutFile $zip
  if (Test-Path $PY_DIR) { Remove-Item -Recurse -Force $PY_DIR }
  Expand-Archive -Path $zip -DestinationPath $PY_DIR -Force
  Remove-Item $zip

  # Habilitar site-packages en el _pth (descomentar "import site")
  $pth = Get-ChildItem -Path $PY_DIR -Filter "python*._pth" | Select-Object -First 1
  if ($pth) {
    (Get-Content $pth.FullName) `
      -replace '^#\s*import site', 'import site' |
      Set-Content $pth.FullName
  }
  Write-Host "      Python instalado en $PY_DIR"
} else {
  Write-Host "[1/4] Python ya presente. OK."
}

# 2) Instalar pip
$pipModule = Join-Path $PY_DIR "Lib\site-packages\pip"
if (-not (Test-Path $pipModule)) {
  Write-Host "[2/4] Instalando pip..."
  $getpip = Join-Path $env:TEMP "get-pip.py"
  Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getpip
  & $PY_EXE $getpip --no-warn-script-location
  Remove-Item $getpip
} else {
  Write-Host "[2/4] pip ya presente. OK."
}

# 3) Instalar dependencias
Write-Host "[3/4] Instalando dependencias (Flask)..."
& $PY_EXE -m pip install --no-warn-script-location -r (Join-Path $ROOT "requirements.txt")

# 4) Inicializar base de datos
Write-Host "[4/4] Inicializando base de datos..."
Push-Location $ROOT
& $PY_EXE (Join-Path $ROOT "database.py")
Pop-Location

# 5) Pre-descargar el modelo Whisper (opcional, evita esperar al primer uso)
$descargarModelo = Read-Host "¿Pre-descargar el modelo STT (Whisper small, ~244 MB)? [s/N]"
if ($descargarModelo -match '^[sSyY]') {
  Write-Host "[5/5] Descargando modelo Whisper small..."
  & $PY_EXE -c "import stt; stt._ensure_model(); print('Modelo listo.')"
} else {
  Write-Host "[5/5] Modelo se descargará en el primer uso (~10-30s)."
}

Write-Host ""
Write-Host "================================================================"
Write-Host "  Instalación completada."
Write-Host "  Para iniciar el servidor:  doble click en  iniciar.bat"
Write-Host "  El dictado por voz (STT) está disponible en notas y motivos."
Write-Host "================================================================"
Read-Host "Presiona Enter para salir"
