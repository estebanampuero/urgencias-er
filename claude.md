# Contexto del proyecto para Claude Code. Lee esto primero antes de tocar nada.

## Qué es esto
Sistema web de entrega de turno para urgencias hospitalarias.
On-premise, Flask + SQLite, sin overengineering. Ver ARQUITECTURA.md para detalle completo.

## Stack (no cambiar sin razón)
- Python 3.11, Flask 3.0.3, waitress, Jinja2 (SSR, sin React)
- SQLite + WAL, sin ORM (sqlite3 raw)
- Vanilla JS + CSS, sin build step
- faster-whisper (STT local), Claude Haiku (LLM opcional)

## Archivos clave
- app.py (~1200 LOC) — rutas, decorators, glue
- database.py — schema y migraciones idempotentes
- triage.py — algoritmo ESI (NO tocar sin correr tests)
- alertas.py, llm.py, stt.py, busqueda.py, fhir.py

## Reglas importantes
- Toda ruta nueva necesita @login_required + @requiere_permiso("X")
- pacientes.turno_id NUNCA cambia tras registro
- Migraciones en database.py deben ser idempotentes
- Tests: pytest tests/ -v → debe pasar 39 casos siempre

## Patrón para rutas nuevas
@app.route("/algo", methods=["POST"])
@requiere_permiso("nombre_permiso")
def algo():
    ...

## Deploy
- Local: python serve.py → http://127.0.0.1:5050
- VPS: ssh root@5.252.52.19, docker compose -f docker-compose.prod.yml up -d --build
- Deploy manual por scp o git pull en VPS (no hay CI/CD)
