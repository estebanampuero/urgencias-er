# Sistema de Entrega de Turno · Servicio de Urgencias

Aplicación web on-premise para registrar pacientes, notas clínicas y pendientes
durante un turno, y generar una **entrega de turno imprimible** al cierre.

- **Stack**: Python 3.11 + Flask + SQLite — sin dependencias en la nube.
- **Despliegue**: una sola carpeta autocontenida en un PC servidor del hospital.
- **Acceso**: el resto del equipo entra por navegador en la red local.
- **HIS**: integración futura con *Sistemas Expertos (SEIS)* vía endpoints `/api/`.

---

## 1. Desarrollo (Mac / Linux)

```bash
cd er
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Abre `http://127.0.0.1:5050`.

La base de datos se crea automáticamente en `data/urgencias.db` con usuarios
de ejemplo (médicos, EU y TENS) — modifícalos desde `/usuarios`.

---

## 2. Despliegue en Windows (PC del hospital)

**Requisitos**: Windows 10/11, conexión a internet **solo la primera vez**
para descargar Python portable. No requiere permisos de administrador.

### Pasos

1. Copia toda la carpeta `er/` al PC servidor (ej: `C:\Urgencias\`).
2. Ejecuta una sola vez:
   - Click derecho sobre `setup.ps1` → **Ejecutar con PowerShell**
   - O desde una terminal:
     ```powershell
     powershell -ExecutionPolicy Bypass -File .\setup.ps1
     ```
   Esto descarga Python 3.11 embeddable, instala pip, Flask y crea la base
   de datos. Tarda ~1 minuto.
3. Para arrancar el servidor:
   - **Doble click en `iniciar.bat`** (más simple)
   - O `iniciar.ps1` desde PowerShell.

Al iniciar, la consola muestra la IP en LAN:

```
  Local:    http://127.0.0.1:5050
  Red LAN:  http://192.168.1.42:5050
```

Comparte la URL de **Red LAN** con el resto del equipo del turno — no
necesitan instalar nada, solo abrir esa URL en su navegador.

### Inicio automático (opcional)

Para que el servidor arranque al encender el PC del hospital:

1. `Win+R` → `shell:startup` → Enter
2. Arrastra un acceso directo a `iniciar.bat` dentro de esa carpeta.

### Firewall

La primera vez que arranque el servidor, Windows pedirá permiso de firewall.
Acepta **Redes privadas** (red del hospital).

---

## 3. Funcionalidades

### Turnos
- Apertura con tipo (día 08–20 / noche 20–08), médico jefe, EU, TENS.
- Solo un turno activo a la vez.
- Cierre con notas → genera entrega de turno automáticamente.

### Pacientes
- Datos demográficos, categoría ESI (C1–C5), box, signos vitales, motivo,
  antecedentes y alergias.
- Cambio de estado: en atención / alta / hospitalizado / traslado / fugado / fallecido.
- Edición de datos clínicos.

### Signos vitales
- Toma inicial al registrar.
- Tomas seriadas posteriores con autor y timestamp.
- Historial completo por paciente.

### Notas clínicas y pendientes
- Notas libres con autor y rol.
- Pendientes tipificados (examen / interconsulta / traslado / medicamento / otro)
  con estado: pendiente / en curso / completado / cancelado.

### Entrega de turno
- Resumen automático imprimible (`Ctrl+P` o botón **Imprimir / PDF**).
- Por paciente: ESI, box, motivo, antecedentes, alergias, últimos SV,
  pendientes abiertos y últimas 3 notas.
- Estadísticas: total, por ESI, altas/hosp./traslados.
- Líneas de firma médico/EU entrante y saliente.

### Historial y dashboard
- Historial de turnos cerrados con acceso a su entrega.
- Dashboard con estadísticas en vivo del turno activo (auto-refresh 60s).

### Dictado por voz (STT local, español clínico)
Botón micrófono 🎙 en motivos, antecedentes, alergias, notas clínicas, pendientes
y notas de apertura/cierre de turno.

- Motor: **`faster-whisper small`** (int8) corriendo 100 % local en CPU.
- Idioma: español. Vocabulario clínico inyectado por contexto
  (motivo, antecedentes, alergias, nota, pendiente, cierre).
- Audio capturado en el navegador, enviado al servidor, transcrito y devuelto.
  El archivo de audio se borra al instante; **nada sale del PC servidor**.
- Modelo en disco: ~244 MB, descarga automática la primera vez (o
  pre-descargado por `setup.ps1` si aceptas la pregunta).
- Velocidad típica: 9 s de audio → ~3 s de transcripción en CPU moderna.
- Requisitos: navegador con permiso de micrófono (Chrome / Edge / Safari).
- Si `faster-whisper` no se instala, los botones mic simplemente no aparecen.

---

## 4. Integración futura con SEIS (HL7)

Endpoints reservados en `app.py`:

- `GET  /api/turnos/activo` → turno activo en JSON.
- `GET  /api/pacientes/turno/<id>` → pacientes del turno.
- `GET  /api/paciente/<id>` → paciente + notas + pendientes + SV.
- `POST /api/seis/ingreso` → reservado para mensajes HL7 ADT^A04
  desde el HIS. Hoy responde `501 Not Implemented`.

Cuando se conecte la integración, mapear los segmentos PID / PV1 / OBX
del HL7 a las columnas de `pacientes` y `signos_vitales`.

---

## 4.5 Datos de demostración

Para mostrar la plataforma con datos realistas (1 semana completa de
turnos, 19 profesionales, ~30 pacientes con notas/SV/pendientes):

```bash
.venv/bin/python seed_demo.py     # Mac/Linux dev
python\python.exe seed_demo.py    # Windows portable
```

⚠️ El script **borra todos los turnos y pacientes existentes** antes de
generar la data demo. No usar en producción si hay datos reales.

## 5. Backup

Toda la información vive en **`data/urgencias.db`** (archivo SQLite).
Para respaldar: copia ese archivo a una unidad externa o carpeta de red.

Recomendado: tarea programada de Windows que copie `data/urgencias.db` a
una carpeta de respaldo cada 1–6 horas.

---

## 6. Estructura

```
er/
├── app.py              # Flask: rutas, lógica
├── database.py         # Schema SQLite + seed
├── requirements.txt
├── setup.ps1           # Instalación primera vez (Windows)
├── iniciar.bat         # Arranque rápido (Windows)
├── iniciar.ps1         # Arranque alternativo (Windows PS)
├── data/               # Base de datos (creada al iniciar)
├── templates/          # Jinja2
├── static/css|js/      # Estilos y JS
└── python/             # Python portable (creado por setup.ps1)
```

---

## 7. Seguridad

Pensado para red LAN cerrada del hospital. El "login" es selector de
usuario sin contraseña — la autenticación seria se delega al HIS cuando
se integre. Si se requiere endurecer:

- Añadir `WERKZEUG_RUN_MAIN` con servidor WSGI tipo `waitress` (más
  robusto que el dev-server de Flask en producción).
- Hash de contraseñas con `werkzeug.security` (ya incluido).
- HTTPS local con un certificado autofirmado si exige el hospital.
