# Sistema de Entrega de Turno · Urgencias

Aplicación web para el servicio de urgencias hospitalario: registro de
pacientes, signos vitales, notas clínicas, pendientes, **categorización
ESI explicable**, **dictado por voz local** y **entrega de turno
imprimible/PDF**.

- Stack: Python 3.11 + Flask + SQLite + faster-whisper + Anthropic Claude.
- Despliegue: contenedor Docker, escala en EasyPanel o cualquier host
  compatible con Docker (Fly.io, Railway, Render, VPS propio).
- Datos: 100% on-premise. STT corre local. Solo los endpoints LLM
  opcionales contactan a Anthropic.

## Quickstart en EasyPanel

→ Ver guía completa paso a paso: **[GUIA_EASYPANEL.md](./GUIA_EASYPANEL.md)**

Resumen ultracorto:

1. En EasyPanel crear servicio tipo **App** apuntando a este repo de
   GitHub. EasyPanel detecta el `Dockerfile` automáticamente.
2. Setear las env vars de `.env.example` (mínimo: `SECRET_KEY`,
   opcional: `ANTHROPIC_API_KEY`, `SEED_DEMO=true` solo en el primer
   arranque).
3. Montar volumen persistente en `/app/data`.
4. Asignar dominio, EasyPanel emite TLS automático.

---

## Desarrollo local

```bash
git clone https://github.com/estebanampuero/urgencias-er
cd urgencias-er
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python seed_demo.py    # poblar BD demo
.venv/bin/python serve.py        # arranca en :5050
```

Abrir `http://127.0.0.1:5050`.

Tests:
```bash
.venv/bin/python -m pytest tests/ -v
```

## Funcionalidades

### Triage ESI (chileno, MINSAL)
- Algoritmo en `triage.py` (240 LOC, **39 tests pasando**).
- Sugerencia en vivo mientras se llena el form del paciente.
- Razones explícitas por categoría visibles al médico.
- Banner aceptar/cambiar en ficha del paciente.

### STT local en español clínico
- `faster-whisper small int8` corriendo CPU.
- Botón micrófono en motivo, antecedentes, notas, pendientes.
- Prompt con vocabulario clínico chileno para mejor reconocimiento.
- Audio borrado tras transcribir, modelo en `data/whisper-cache/`.

### LLM (opcional, requiere `ANTHROPIC_API_KEY`)
- Resumen narrativo de entrega de turno (Claude Haiku 4.5).
- Segunda opinión sobre categorización ESI.
- Prompt caching del léxico clínico.

### Handoff entre turnos
- Al abrir un turno nuevo, los pacientes "en atención" del turno
  anterior se reciben automáticamente.
- Nota clínica automática con timestamp y equipo entrante.
- Trazabilidad preservada (turno_id original no cambia).

### Detección de patrones SV
- Alertas automáticas al registrar SV: deterioro de PA, taquicardia
  progresiva, hipoxemia, deterioro neurológico, hipertermia mantenida,
  shock index ≥ 1.0.
- Dashboard global de alertas activas del turno.
- Severidad info/warn/critico con animación visual.

### Búsqueda semántica
- TF-IDF sobre notas clínicas históricas.
- Reindex on-demand para admins.
- Apta hasta ~10k notas; reemplazable con pgvector después.

### Integración HIS / FHIR R4
- `/fhir/Patient/<id>`, `/fhir/Observation` (POST), `/fhir/metadata`.
- Endpoint para dispositivos médicos con auth por `X-Device-Key`.
- `/api/seis/ingreso` recibe `Patient` FHIR de SEIS.

### Seguridad
- `@login_required` en 31 de 37 endpoints (los 6 sin auth son
  públicos por diseño: login, logout, healthz, fhir/metadata, fhir
  observation con device key).
- **RBAC** con 4 roles (admin/medico/eu/tens) y 13 permisos.
- **CSRF protection** vía flask-wtf en todos los forms.
- **Rate limiting** con flask-limiter:
  - Login: 10/min
  - Sugerir ESI: 60/min
  - Transcribir: 20/min, 200/hr
  - LLM resumen: 10/hr
  - LLM triage: 30/hr
- `SECRET_KEY` persistida con `secrets.token_bytes(32)` si no se setea
  como env.
- `MAX_CONTENT_LENGTH = 30 MB`.
- `SESSION_COOKIE_HTTPONLY`, `SAMESITE=Lax`.
- Authorization scoping: pacientes solo modificables si están en turno
  activo o en atención.

### Auditoría
- Tabla `auditoria` con actor, acción, recurso, IP, timestamp.
- Vista `/auditoria` (solo admin) con últimas 200 entradas.
- Eventos auditados: cambio de estado, aceptar ESI, cerrar turno,
  export PDF, ingreso FHIR, generación de alertas, reconocer alerta.

### Backup
- Backup automático tras cada cierre de turno.
- Rotación configurable con `BACKUP_KEEP` (default 30).
- Backup online SQLite (no requiere apagar el server).

### Infrastructure
- `serve.py` con waitress multi-thread.
- SQLite con **WAL mode**, busy_timeout 5s, 11 índices.
- Healthcheck minimal en `/healthz` (sin info sensible).
- Detalles en `/api/admin/status` (auth requerida).
- **Logs JSON estructurados** por stdout (compatible con EasyPanel/Loki/etc).

## Estructura

```
urgencias-er/
├── app.py              # Flask app principal (1100 LOC)
├── database.py         # Schema SQLite + migraciones
├── triage.py           # Algoritmo ESI chileno
├── stt.py              # Whisper local
├── llm.py              # Claude Haiku integration
├── alertas.py          # Detección de patrones SV
├── busqueda.py         # TF-IDF semantic search
├── fhir.py             # Adaptadores HL7 FHIR R4
├── backup.py           # Backup automático
├── serve.py            # waitress WSGI entrypoint
├── seed_demo.py        # Datos demo (1 semana, 34 pac)
├── tests/              # 39 tests pytest
├── templates/          # 14 Jinja templates
├── static/             # CSS + JS
├── Dockerfile          # Multi-stage build
├── docker-compose.yml  # Test local
├── .env.example        # Plantilla de env vars
├── GUIA_EASYPANEL.md   # Deploy paso a paso
└── README.md
```

## Roles y permisos

| Permiso | admin | medico | eu | tens |
|---|---|---|---|---|
| abrir_turno | ✓ | ✓ | | |
| cerrar_turno | ✓ | ✓ | | |
| crear_paciente | ✓ | ✓ | ✓ | |
| editar_paciente | ✓ | ✓ | ✓ | |
| cambiar_estado | ✓ | ✓ | ✓ | |
| aceptar_esi | ✓ | ✓ | | |
| agregar_sv | ✓ | ✓ | ✓ | ✓ |
| agregar_nota | ✓ | ✓ | ✓ | ✓ |
| agregar_pendiente | ✓ | ✓ | ✓ | |
| ver_alertas | ✓ | ✓ | ✓ | ✓ |
| exportar_pdf | ✓ | ✓ | ✓ | |
| gestionar_usuarios | ✓ | | | |
| ver_auditoria | ✓ | | | |

## Licencia y disclaimer

Esto es un proyecto de software libre para uso interno hospitalario. **No
es un dispositivo médico certificado**. Las sugerencias de triage son
herramientas de apoyo a la decisión clínica, NO reemplazan el juicio del
profesional. Antes de usar con datos reales:

1. Validar el algoritmo de triage contra protocolos del servicio.
2. Configurar `SECRET_KEY`, deshabilitar `SEED_DEMO`.
3. Implementar SSO/contraseñas reales antes de exponer públicamente.
4. Cumplir Ley 19.628 (Chile) y demás regulaciones aplicables.
