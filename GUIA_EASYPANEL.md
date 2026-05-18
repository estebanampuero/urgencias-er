# Guía paso a paso · Deploy en EasyPanel

Esta guía cubre desde el push del código hasta tener la app corriendo en
tu VPS bajo EasyPanel con dominio HTTPS automático.

## Pre-requisitos

- VPS con EasyPanel instalado y accesible.
- Cuenta GitHub (este repo asume `github.com/estebanampuero/urgencias-er`).
- Un (sub)dominio apuntado al VPS, ej. `urgencias.midominio.com`. EasyPanel
  emite el certificado TLS automáticamente vía Let's Encrypt.

---

## Paso 1 · Crear el servicio en EasyPanel

1. Entrar al panel de EasyPanel en `https://<tu-vps>:3000`.
2. Crear un nuevo **Project** llamado `urgencias` (o el que prefieras).
3. Dentro del project: **+ Service → App**.
4. Configurar:
   - **Name**: `urgencias-er`
   - **Source**: GitHub
   - **Repository**: `estebanampuero/urgencias-er`
   - **Branch**: `main`
   - **Build Path**: `/` (raíz del repo)
   - **Build Method**: `Dockerfile`
   - **Dockerfile Path**: `Dockerfile`

EasyPanel detectará el `Dockerfile` automáticamente y construirá la imagen.

---

## Paso 2 · Variables de entorno

En la pestaña **Environment** del servicio, agregá:

| Variable | Valor | Notas |
|---|---|---|
| `SECRET_KEY` | (generar) | `openssl rand -hex 32` en tu terminal y pegar |
| `PORT` | `5050` | puerto interno |
| `THREADS` | `8` | ajustar según CPU del VPS |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | opcional, para LLM features |
| `LLM_MODEL` | `claude-haiku-4-5-20251001` | opcional |
| `SEED_DEMO` | `true` | **solo en primer arranque**; después cambiar a `false` o vaciar |
| `BACKUP_KEEP` | `30` | cantidad de backups rotados |

> Tras el primer deploy con `SEED_DEMO=true`, cambialo a `false` y redeploya
> para que no intente sembrar otra vez (el código verifica si la BD ya tiene
> datos antes de poblar, pero es buena higiene).

---

## Paso 3 · Volumen persistente

EasyPanel **debe** montar un volumen en `/app/data` para que la BD no se
pierda en cada redeploy.

En la pestaña **Mounts** o **Storage** del servicio:

- **Mount Type**: Volume
- **Volume Name**: `urgencias_data`
- **Mount Path**: `/app/data`

Esto persiste:
- `data/urgencias.db` (SQLite)
- `data/whisper-cache/` (modelo Whisper small, ~244 MB)
- `data/backups/` (backups rotados)
- `data/.secret_key` (clave persistida si no se setea env)

---

## Paso 4 · Dominio y HTTPS

En la pestaña **Domains** del servicio:

- **Add Domain**: `urgencias.midominio.com`
- **Port**: `5050`
- **HTTPS**: activado (EasyPanel pide cert a Let's Encrypt automáticamente)

> Asegurate de que el DNS `A` de `urgencias.midominio.com` apunte a la IP del
> VPS antes de activar HTTPS, o el ACME challenge fallará.

---

## Paso 5 · Recursos (opcional pero recomendado)

EasyPanel → **Resources**:

- **Memory**: 1024 MB mínimo (Whisper small carga ~500 MB en RAM)
- **CPU**: 1 vCPU mínimo

Para hospitales con tráfico real, subir a 2 GB / 2 vCPU.

---

## Paso 6 · Primer deploy

1. Click **Deploy** en EasyPanel.
2. Ver logs en tiempo real en la pestaña **Logs**. Esperar a que aparezca:
   ```
   serving on http://0.0.0.0:5050
   ```
3. Visitar `https://urgencias.midominio.com/healthz`. Debería responder:
   ```json
   { "ok": true, "db": {...}, "version": "1.0.0" }
   ```
4. Visitar `https://urgencias.midominio.com/` y elegir un usuario del seed
   (por ejemplo, **Dr. María González**).

---

## Paso 7 · Después del primer deploy

1. En **Environment**, cambiar `SEED_DEMO` a `false` (o vaciar). Redeploy.
2. Ir a `/usuarios` como admin y crear las cuentas reales de tu equipo.
3. Desactivar (o eliminar) los usuarios demo.

---

## Auto-deploy

Por default EasyPanel re-deploya al recibir un webhook de push a `main`. En
**Settings → Auto Deploy** del servicio, asegurate de que esté activado.

Para que tu amigo modifique la app:
1. Clonar: `git clone https://github.com/estebanampuero/urgencias-er`
2. Hacer cambios localmente.
3. Push a `main` → EasyPanel re-deploya solo.

---

## Backups

El sistema crea backups automáticos en `data/backups/` cada vez que se
cierra un turno. Para backup off-VPS (recomendado):

```bash
# desde tu máquina, vía SSH al VPS
rsync -avz vps:/var/lib/docker/volumes/urgencias_data/_data/backups/ ./backups-local/
```

Programar en cron.

---

## Logs y troubleshooting

| Síntoma | Causa probable | Solución |
|---|---|---|
| `/healthz` → 502 | Container no arranca | Ver logs en EasyPanel, buscar tracebacks Python |
| BD vacía tras redeploy | Volumen no persistente | Revisar mount `/app/data` |
| STT no funciona | Modelo no descargado | Ver logs, primera llamada baja ~244 MB (~30s) |
| LLM no funciona | `ANTHROPIC_API_KEY` no seteada | Ver `/api/llm/status` |
| `pip install` falla en build | Memoria insuficiente | Subir RAM del VPS o agregar swap |

---

## Actualizaciones

Para deploy de nueva versión:

```bash
git pull origin main           # tu amigo o vos
# editar
git commit -am "fix: ..."
git push origin main
# EasyPanel detecta el push y redeploya
```

Los cambios de schema corren automáticamente vía `_migrate()` en cada
arranque (idempotente).

---

## Costos esperados

- **VPS**: $5-10/mes (1 vCPU, 2 GB RAM) — Hetzner, DigitalOcean, Vultr.
- **Dominio**: $10/año.
- **TLS**: gratis (Let's Encrypt via EasyPanel).
- **LLM (opcional)**: ~$0.002/turno con Haiku 4.5 si activás resumen.
- **Total mensual**: <$15 para uso real.
