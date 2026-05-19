# Arquitectura · Sistema de Entrega de Turno

> Documento técnico para desarrolladores. Para visión de usuario clínico
> ver [MANIFIESTO.md](./MANIFIESTO.md). Para Claude Code ver [CLAUDE.md](./CLAUDE.md).

---

## 1. Visión general

Sistema web para el servicio de urgencias hospitalario. Reemplaza la
entrega de turno informal (papel, memoria, WhatsApp) por un sistema
auditable, con triage explicable, dictado por voz local y handoff
automático entre turnos.

**Principios de diseño:**

1. **On-premise por default**. Datos, STT y BD viven en el servidor del
   hospital. Cloud (LLM Anthropic) es opt-in.
2. **Sin overengineering**. Flask + SQLite alcanza para 200 pacientes/día.
   Nada de microservicios, K8s ni colas distribuidas.
3. **AI asistido, no autónomo**. El triage sugiere; el humano decide.
4. **Idempotente**. Migraciones de schema, seeds, deploys — todo
   re-ejecutable sin romper.
5. **Trazabilidad clínica**. Toda mutación importante queda en `auditoria`.

---

## 2. Stack

| Capa | Tecnología | Por qué |
|---|---|---|
| Runtime | Python 3.11 | Standard, slim Docker image, soporta faster-whisper |
| Web framework | Flask 3.0.3 | Suficiente para 37 rutas, sin curva |
| WSGI server | waitress 3.0.0 | Multi-thread, Windows-friendly, sin C deps |
| Templates | Jinja2 (Flask) | Server-side rendering, no SPA |
| BD | SQLite + WAL | Una sola instancia, hasta ~10k pac/año sin pestañear |
| ORM | sqlite3 raw + helpers | Conscientemente sin ORM (40 LOC menos de magia) |
| STT | faster-whisper small int8 | Español clínico local en CPU |
| LLM | Anthropic Claude Haiku 4.5 | Resumen entrega + segunda opinión ESI |
| Auth | Flask sessions + RBAC custom | Sin OAuth/JWT por ahora; LAN-only |
| CSRF | flask-wtf 1.2.1 | Token en forms + header en JS |
| Rate limit | flask-limiter 3.8.0 | Memory backend (suficiente single-replica) |
| PDF | weasyprint 62.3 | Renderiza el mismo HTML de la entrega |
| Frontend | Vanilla JS + CSS (~300+935 LOC) | Sin React, sin build step |
| Proxy/TLS | Traefik 3.6.7 (EasyPanel) | Lets Encrypt automático |
| Despliegue | Docker + EasyPanel + VPS Contabo | Self-hosted, control total |

---

## 3. Diagrama de bloques

```
                         ┌───────────────────────────┐
   Browser (usuario)  ←──┤  Traefik 3.6.7 + TLS R12  │  Internet
                         └─────────────┬─────────────┘
                                       │ HTTP interno red docker overlay
                                       ▼
                         ┌───────────────────────────┐
                         │  Container urgencias-er   │
                         │                           │
                         │  waitress :5050 (8 thr)   │
                         │       │                   │
                         │       ▼                   │
                         │  Flask app.py             │
                         │  ┌─────────────────────┐  │
                         │  │ before_request:     │  │
                         │  │  carga g.usuario,   │  │
                         │  │  g.turno_activo     │  │
                         │  └──────────┬──────────┘  │
                         │             │             │
                         │  ┌──────────┴──────────┐  │
                         │  │ 37 rutas            │  │
                         │  │ @login_required     │  │
                         │  │ @requiere_permiso   │  │
                         │  │ @limiter.limit      │  │
                         │  │ @csrf protect       │  │
                         │  └──────────┬──────────┘  │
                         │             │             │
                         │  ┌──────────┴──────────┐  │
                         │  │ Módulos de dominio: │  │
                         │  │ ├─ database.py      │  │
                         │  │ ├─ triage.py        │  │
                         │  │ ├─ stt.py           │  │
                         │  │ ├─ llm.py           │  │
                         │  │ ├─ alertas.py       │  │
                         │  │ ├─ busqueda.py      │  │
                         │  │ ├─ fhir.py          │  │
                         │  │ └─ backup.py        │  │
                         │  └──────────┬──────────┘  │
                         └─────────────┼─────────────┘
                                       │
                         ┌─────────────▼─────────────┐
                         │  Volumen urgencias_data   │
                         │  ├─ urgencias.db (SQLite) │
                         │  ├─ whisper-cache/        │
                         │  ├─ backups/              │
                         │  └─ .secret_key           │
                         └───────────────────────────┘
```

---

## 4. Módulos Python

| Archivo | Líneas | Responsabilidad |
|---|---|---|
| `app.py` | ~1200 | Rutas, decorators de seguridad, glue entre módulos |
| `database.py` | 175 | Schema SQLite, migraciones idempotentes, `get_conn()` |
| `triage.py` | 240 | Algoritmo ESI chileno (reglas puras, sin estado) |
| `stt.py` | 115 | faster-whisper lazy-loaded, lockeo de carga |
| `llm.py` | 220 | Anthropic SDK + prompt caching del léxico clínico |
| `alertas.py` | 130 | Detección de patrones SV (reglas puras) |
| `busqueda.py` | 160 | TF-IDF + similitud coseno, sin deps externas |
| `fhir.py` | 175 | Adaptadores HL7 FHIR R4 ↔ schema interno |
| `backup.py` | 55 | Backup online SQLite + rotación |
| `serve.py` | 60 | Entrypoint waitress + seed + preload |
| `seed_demo.py` | 470 | Genera 1 semana demo (idempotente) |

**Por qué archivos planos y no paquetes**: a este tamaño (~3500 LOC totales)
la fricción de tener `services/`, `models/`, `routes/`, `repositories/` es
mayor que el beneficio. Cuando `app.py` pase de ~1500 LOC, refactorizar.

---

## 5. Modelo de datos (SQLite)

```
usuarios (19)
   id, nombre, rol [admin|medico|eu|tens], rut, activo
        │
        ├──→ turnos (14)
        │      id, tipo [dia|noche], fecha_inicio, fecha_cierre,
        │      medico_jefe_id → usuarios.id
        │      eu_id          → usuarios.id
        │      tens_ids       (JSON array, denormalized)
        │      notas_apertura, notas_cierre, estado [activo|cerrado]
        │
        └──→ pacientes (34)
               id, turno_id → turnos.id (turno donde fue REGISTRADO)
               nombre, rut, edad, sexo, categoria_esi [C1-C5]
               box, motivo_consulta, antecedentes, alergias
               pa, fc, fr, temp, sato2, glasgow, hgt (snapshot actual)
               estado [en_atencion|alta|hospitalizado|traslado|fallecido|fugado]
               ingreso, egreso, creado_por → usuarios.id
               esi_sugerido, esi_razones (JSON array)
               hospital_id (multi-tenant base, no scopeado aún)
                  │
                  ├──→ signos_vitales (86)
                  │      id, paciente_id, pa, fc, fr, temp, sato2,
                  │      glasgow, hgt, eva, autor_id, creado_en
                  │
                  ├──→ notas (56)
                  │      id, paciente_id, contenido, autor_id, creado_en
                  │
                  ├──→ pendientes (53)
                  │      id, paciente_id, tipo, descripcion,
                  │      estado, creado_en, completado_en, autor_id
                  │
                  └──→ alertas_sv
                         id, paciente_id, tipo, severidad,
                         mensaje, datos (JSON), reconocida,
                         reconocida_por → usuarios.id, reconocida_en

auditoria               (read-only desde la UI)
  actor_id, accion, recurso, recurso_id, detalle (JSON),
  ip, ts

hospitales              (multi-tenant base, no activo aún)
dispositivos            (FHIR Observation auth por API key)
notas_idx + busqueda_idf (TF-IDF para búsqueda semántica)
```

**Índices**: `pacientes(turno_id)`, `pacientes(estado)`,
`pacientes(estado, turno_id)`, `turnos(estado)`,
`notas(paciente_id)`, `notas(creado_en)`,
`pendientes(paciente_id)`, `pendientes(estado)`,
`signos_vitales(paciente_id)`, `signos_vitales(creado_en)`,
`auditoria(ts)`, `auditoria(recurso, recurso_id)`,
`alertas_sv(paciente_id)`, `alertas_sv(reconocida)`,
`dispositivos(api_key)`.

**Decisión clave (handoff)**: `pacientes.turno_id` NUNCA cambia tras
registrar al paciente. Apunta siempre al turno donde fue registrado
originalmente. El "handoff" entre turnos se resuelve en la query:

```sql
-- Pacientes que ve el turno activo:
SELECT * FROM pacientes
WHERE turno_id = :activo                                  -- propios
   OR (estado = 'en_atencion' AND turno_id != :activo)    -- recibidos
```

Esto preserva la historia clínica y permite reconstruir cualquier turno
viejo con sus pacientes originales.

---

## 6. RBAC (Role-Based Access Control)

Definido en `app.py:ROLE_PERMS`. Cuatro roles, 13 permisos.

| Permiso | admin | medico | eu | tens |
|---|:-:|:-:|:-:|:-:|
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

**Cómo aplicarlo en una ruta nueva:**

```python
@app.route("/algo", methods=["POST"])
@requiere_permiso("nombre_del_permiso")
def algo():
    ...
```

El decorator redirige a `/login` si no hay sesión, o aborta con 403 si
hay sesión pero falta el permiso. Templates leen `tiene_permiso('X')`
para ocultar/mostrar links del menú.

---

## 7. Flujos clínicos clave

### 7.1 Turno + paciente, ciclo completo

```
1. Médico jefe → /turno/nuevo
   ├─ POST crea turno (estado='activo')
   ├─ Identifica pacientes con estado='en_atencion' de turnos previos
   ├─ Agrega nota automática a cada uno: "⇄ Paciente recibido por turno..."
   └─ Flash: "Turno abierto. Se recibieron N paciente(s)..."

2. EU → /paciente/nuevo
   ├─ Llena form (datos, SV inicial, motivo)
   ├─ JS llama /api/sugerir-esi debounced → muestra sugerencia
   ├─ EU acepta sugerencia o elige distinto
   └─ POST inserta paciente + 1 toma de SV inicial + sugerencia guardada

3. EU/TENS → /paciente/<id>
   ├─ Agrega SV → trigger alertas.evaluar_paciente()
   │   └─ Si detecta deterioro → crea fila en alertas_sv + flash warning
   ├─ Agrega nota (con dictado opcional /api/transcribir)
   └─ Agrega/cierra pendientes

4. Médico jefe → /turno/<id>/cerrar
   ├─ POST cambia estado='cerrado', guarda notas_cierre
   ├─ audit() registra el cierre
   └─ backup.do_backup() automático
   → Redirect a /turno/<id>/entrega para impresión

5. Próximo médico → /turno/nuevo (vuelve a paso 1)
```

### 7.2 Pipeline de un paciente

```
en_atencion ─┬─→ alta (egreso ahora)
             ├─→ hospitalizado (egreso ahora)
             ├─→ traslado (egreso ahora)
             ├─→ fugado (egreso ahora)
             └─→ fallecido (egreso ahora)
                  ↓
              egreso != NULL bloquea ediciones
              (autorizado_o_404)
```

### 7.3 Sugerencia ESI en vivo (form de paciente)

```
JS observa cambios en motivo/antecedentes/edad/SV
   │ debounce 350ms
   ▼
fetch POST /api/sugerir-esi {json campos}
   │
   ▼
triage.sugerir_categoria(datos)
   │  evalúa en cascada C1→C2→C3→C4→C5
   │  retorna (categoria, [razones])
   ▼
JS pinta hint con categoría + 1ª razón + botón "Usar sugerencia"
```

---

## 8. Triage ESI · algoritmo

Archivo: `triage.py`. Tests: `tests/test_triage.py` (39 casos, todos pass).

**Cascada de evaluación** (primera categoría que matchea retorna):

```
C1 RESUCITACIÓN (amenaza vital inmediata):
  - Glasgow ≤8
  - SatO2 <90
  - PA sistólica <80
  - FC >150 o <40
  - FR >35 o <8
  - Temp <34
  - Keywords motivo/antecedente: PCR, shock, politrauma, status epiléptico, anafilaxia

C2 EMERGENCIA (riesgo vital potencial):
  - Glasgow <15
  - SatO2 <94
  - PA <90 o >220
  - FC >130 o <50
  - FR >30 o <10
  - Temp ≥39.5 o <35
  - Keywords: IAM, ACV, sepsis, dolor torácico, disnea severa, hemorragia, intento suicida, TEC moderado/grave
  - Modificador: cardiópata + dolor torácico = C2 aunque SV normales

C3 URGENCIA:
  - Temp ≥38.5
  - FC >110 o <55
  - PA <100 o >180
  - SatO2 <96
  - Keywords: dolor abdominal, cefalea intensa, cólico renal, lumbago, asma, crisis HTA
  - Edad <2 o ≥75 (factor de riesgo etario)

C4 MENOR:
  - Esguince, sutura, faringitis, otitis, ITU baja, conjuntivitis, picadura, herida

C5 NO URGENTE:
  - "control", "curación", "patología crónica"
```

**Datos guardados**: cada paciente tiene `esi_sugerido` y `esi_razones`
(JSON array). El médico puede aceptarlo (botón en ficha) o ignorarlo.

---

## 9. STT · faster-whisper

Archivo: `stt.py`. Modelo: `small` int8, CPU, español.

**Carga**: perezosa. Si `PRELOAD_STT=true` en env, se carga al arrancar
el contenedor para evitar el ~30s de demora del primer request.

**Cache**: `data/whisper-cache/` (244 MB en disco, ~500 MB en RAM).
Sobrevive a re-deploys (vive en el volumen persistente).

**Prompt clínico**: `stt.py:_PROMPTS` tiene 7 contextos
(motivo/antecedentes/alergias/nota/pendiente/cierre/general). El frontend
manda `contexto=...` en el FormData del POST. El initial_prompt sesga al
modelo hacia vocabulario clínico (HTA, DM2, SatO2, etc.).

**Endpoint**: `POST /api/transcribir`, multipart con `audio` (webm/mp4/wav)
y `contexto`. Rate limit: 20/min, 200/hr por IP.

**Calidad observada**: convierte "ciento cuarenta sobre noventa" → "140/90",
"noventa y cinco por ciento" → "95%". Reconoce dolor torácico, hipertensión,
diabetes mellitus.

---

## 10. LLM · Claude Haiku 4.5

Archivo: `llm.py`. Opcional (si no hay `ANTHROPIC_API_KEY`, módulo expone
`available()=False` y endpoints retornan 503).

**Features**:

1. **Resumen de entrega de turno** (`/api/llm/resumen-turno/<id>`):
   acepta la entrega de un turno, genera narrativa estructurada
   priorizando C1/C2 + pendientes urgentes. Rate limit: 10/hr.

2. **Triage complementario** (`/api/llm/triage/<pid>`): segunda opinión
   sobre la categoría ESI ya calculada por reglas. Retorna JSON con
   `{acuerdo, categoria_sugerida, comentario}`. Rate limit: 30/hr.

**Prompt caching**: el léxico clínico (1.3k tokens) va en el bloque
`system` con `cache_control: ephemeral`. La 1ª llamada paga, las
siguientes ~90% más baratas.

---

## 11. FHIR R4 · integración HIS

Archivo: `fhir.py`. Adaptadores schema ↔ FHIR R4.

**Endpoints**:

- `GET /fhir/Patient/<id>` — auth requerida
- `POST /fhir/Observation` — auth por header `X-Device-Key` (tabla
  `dispositivos`). Para que monitores SV publiquen directo.
- `GET /fhir/metadata` — CapabilityStatement
- `POST /api/seis/ingreso` — recibe Patient FHIR del HIS chileno
  Sistemas Expertos (SEIS), crea paciente y aplica triage.

**LOINC codes mapeados** (panel SV):
- 8480-6: PA sistólica
- 8462-4: PA diastólica
- 8867-4: FC
- 9279-1: FR
- 8310-5: Temp
- 59408-5: SatO2
- 9269-2: Glasgow
- 33747-0: HGT

---

## 12. Alertas SV · detección de patrones

Archivo: `alertas.py`. Reglas puras sobre las últimas 2 tomas de SV de
un paciente.

**Reglas implementadas**:

| Tipo | Trigger | Severidad |
|---|---|---|
| deterioro_pa | PA sistólica cae ≥20 mmHg | warn (crítico si PA <90) |
| taquicardia_progresiva | FC sube ≥20 lpm | warn (crítico si FC ≥130) |
| hipoxemia_progresiva | SatO2 cae ≥3% | warn (crítico si SatO2 <90) |
| deterioro_neurologico | GCS cae ≥2 pts | warn (crítico si GCS ≤12) |
| hipertermia_mantenida | Temp ≥38.5 en 2 tomas | warn |
| shock_index_elevado | FC/PA_sis ≥1.0 | warn (crítico si ≥1.3) |

**Trigger**: cada POST a `/paciente/<id>/sv` evalúa al paciente y crea
filas en `alertas_sv`. Dedupe: no crea alertas del mismo tipo si ya
existe una sin reconocer en las últimas 4h.

**UI**:
- Badge animado en el menú con conteo de alertas activas globales.
- Dashboard `/alertas` ordenado por severidad.
- Panel en cada ficha de paciente con sus alertas abiertas + botón
  "Reconocer".

---

## 13. Búsqueda semántica · TF-IDF

Archivo: `busqueda.py`. Implementación sin dependencias externas
(stdlib + nada).

**Cómo**:

1. `rebuild_index()` tokeniza todas las notas (stopwords ES filtradas),
   calcula IDF global, almacena vector TF-IDF por nota en `notas_idx`.
2. `buscar(q)` tokeniza query, vectoriza con IDF global, calcula
   similitud coseno contra todos los vectores guardados.
3. Retorna top-N ordenados por score.

**Escala**: lineal en N notas (full scan). OK hasta ~10k notas. Si
crece más, migrar a SQLite-FTS5 o pgvector.

**Endpoints**:
- `GET /buscar?q=...` — usuario logueado
- `POST /buscar/reindex` — admin (cuando se cargan muchas notas nuevas)

---

## 14. Seguridad

### 14.1 Defense in depth (todas activas)

| Capa | Implementación |
|---|---|
| HTTPS | Traefik + Let's Encrypt R12, redirect 80→443 |
| Sessions | Flask sessions firmadas con `SECRET_KEY` random 32-byte (persistida en `data/.secret_key`) |
| Cookie hardening | HttpOnly, SameSite=Lax |
| CSRF | Flask-WTF en forms + meta tag + JS monkey-patch de fetch |
| Rate limit | flask-limiter (60/min global, específicos por endpoint) |
| MAX_CONTENT_LENGTH | 30 MB (DoS upload) |
| Auth | `@login_required` en 31 de 37 rutas |
| Authorization (RBAC) | `@requiere_permiso(X)` en rutas de mutación |
| Resource scoping | `autorizado_o_404()` en SV/notas/pendientes (paciente debe estar en turno activo o en atención) |
| Audit log | tabla `auditoria` con actor + acción + recurso + IP + ts |

### 14.2 Endpoints sin auth (por diseño)

- `/login`, `/logout` (obvio)
- `/healthz` (Kubernetes/EasyPanel pattern; sin info sensible)
- `/fhir/metadata` (CapabilityStatement público)
- `/fhir/Observation` POST (auth por `X-Device-Key` del dispositivo)
- `/static/*` (assets)

### 14.3 Lo que NO está implementado

- SSO (Active Directory, SAML, OAuth)
- 2FA
- Auditoría con firma criptográfica
- Encriptación at-rest (la BD vive en plain SQLite)
- Reverse proxy con WAF avanzado

Para producción con datos reales de pacientes, evaluar estos según
exigencias del Servicio de Salud.

---

## 15. Despliegue actual (live)

**VPS**: Contabo, IP `5.252.52.19`, Ubuntu 24.04, 6 vCPU AMD EPYC,
11 GB RAM, 193 GB disco.

**EasyPanel** administra Traefik + sus 3 projects (supabase,
transportes-api, transportes-web). `urgencias-er` NO está dentro de
EasyPanel UI (el cap de projects está agotado); corre como container
standalone gestionado vía `docker compose`.

**Routing**: archivo `/etc/easypanel/traefik/config/urgencias.yml`
le dice a Traefik:

```yaml
http:
  routers:
    urgencias:
      rule: "Host(`urgencias.aurik.cl`)"
      entryPoints: [https]
      service: urgencias-service
      tls: { certResolver: letsencrypt }
    urgencias-http:
      rule: "Host(`urgencias.aurik.cl`)"
      entryPoints: [http]
      middlewares: [redirect-to-https]
      service: urgencias-service
  services:
    urgencias-service:
      loadBalancer:
        servers:
          - url: "http://urgencias-er:5050"
  middlewares:
    redirect-to-https:
      redirectScheme:
        scheme: https
        permanent: true
```

El container está conectado a la red overlay `easypanel` para que
Traefik resuelva `urgencias-er` por DNS interno. Esto está declarado
en `docker-compose.prod.yml` como `networks.easypanel.external: true`.

**DNS**: Cloudflare zone `aurik.cl`. Record A
`urgencias → 5.252.52.19`, DNS-only (sin proxy CF para que ACME
http-challenge funcione).

**Volumen**: `urgencias_data` montado en `/app/data/`. Contiene
`urgencias.db`, `whisper-cache/`, `backups/`, `.secret_key`. Persiste
re-deploys.

**Variables de entorno** (en `/opt/urgencias-er/.env`):
```
SECRET_KEY=...
PORT=5050
THREADS=8
SEED_DEMO=true     # cambiar a false después del 1er deploy
PRELOAD_STT=true
BACKUP_KEEP=30
LOG_JSON=true
LOG_LEVEL=INFO
ANTHROPIC_API_KEY=  # opcional
```

---

## 16. Cómo modificar y desplegar

### 16.1 Desarrollo local

```bash
git clone https://github.com/estebanampuero/urgencias-er
cd urgencias-er
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python seed_demo.py     # primera vez, puebla demo
.venv/bin/python serve.py
# Abrir http://127.0.0.1:5050
```

### 16.2 Tests

```bash
.venv/bin/python -m pytest tests/ -v
# Esperado: 39 passed
```

### 16.3 Deploy a VPS (manual, scp directo)

```bash
# Después de cambiar código local:
ssh -i ~/.ssh/contabo_dji root@5.252.52.19 'cd /opt/urgencias-er && git pull'
# o si git push se cuelga:
scp -i ~/.ssh/contabo_dji <archivo> root@5.252.52.19:/opt/urgencias-er/<destino>

# Rebuild + restart:
ssh -i ~/.ssh/contabo_dji root@5.252.52.19 \
  'cd /opt/urgencias-er && docker compose -f docker-compose.prod.yml up -d --build'
```

### 16.4 Logs en vivo

```bash
ssh -i ~/.ssh/contabo_dji root@5.252.52.19 'docker logs -f urgencias-er'
```

### 16.5 Restart sin rebuild

```bash
ssh -i ~/.ssh/contabo_dji root@5.252.52.19 'docker restart urgencias-er'
```

### 16.6 Backup manual

```bash
ssh -i ~/.ssh/contabo_dji root@5.252.52.19 'docker exec urgencias-er python backup.py'
# Backups quedan en data/backups/ dentro del volumen
```

### 16.7 Acceso a la BD

```bash
ssh -i ~/.ssh/contabo_dji root@5.252.52.19 \
  'docker exec -it urgencias-er sqlite3 /app/data/urgencias.db'
```

---

## 17. Roadmap pendiente

| Item | Esfuerzo | Prioridad |
|---|---|---|
| Auto-deploy webhook desde GitHub push | S | media |
| SSO con AD / OAuth | M-L | alta (producción real) |
| Multi-tenant activo (scoping por `hospital_id`) | M | baja (single-hospital ahora) |
| Integración HL7 FHIR real con SEIS | L | alta (es el value-prop comercial) |
| Embeddings + pgvector (búsqueda semántica de verdad) | M | baja (TF-IDF alcanza <10k notas) |
| Dashboards de métricas (KPIs servicio urgencias) | M | media |
| App nativa móvil | XL | descartado |
| Tests E2E (Playwright) | M | media |

---

## 18. Decisiones explícitamente NO tomadas

Cosas que muchos esperarían, deliberadamente fuera de scope:

- **No Postgres**. SQLite alcanza para 10k pacientes/año con WAL.
- **No Redis**. Sesiones Flask en cookie firmada; rate limit memory.
- **No microservicios**. Un monolito Flask de 1200 LOC.
- **No frontend SPA**. Server-side rendering con Jinja, sin build step.
- **No ORM**. sqlite3 raw + helpers.
- **No K8s**. Un contenedor, un volumen.
- **No CI/CD pipeline**. `git push` (cuando funciona) + scp manual.
- **No app móvil nativa**. PWA via responsive web alcanza.

Si alguno de estos se vuelve necesario por crecimiento real (no
hipotético), se revisa.

---

## 19. Referencias

- ESI v4 (Emergency Severity Index): [aliem.com/esi](https://www.aliem.com/esi)
- MINSAL Chile categorización: protocolo del hospital
- HL7 FHIR R4: [hl7.org/fhir/R4](https://www.hl7.org/fhir/R4/)
- Ley 19.628 (datos personales Chile)
- Ley 20.584 (derechos del paciente Chile)
- faster-whisper: github.com/SYSTRAN/faster-whisper
- Anthropic prompt caching: docs.anthropic.com
