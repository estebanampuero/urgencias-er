"""
Sistema de Entrega de Turno - Servicio de Urgencias
Flask + SQLite, despliegue on-premise en LAN hospitalaria.

Ejecuta:  python app.py
Acceso:   http://<IP-servidor>:5000
"""
import os
import json
import socket
import tempfile
import logging
import sys as _sys
from datetime import datetime, time as dtime
from functools import wraps
from typing import Optional


# === Logging estructurado JSON (compatible con stdout de EasyPanel) ===
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in ("args","asctime","created","exc_info","exc_text","filename",
                     "funcName","levelname","levelno","lineno","message","module",
                     "msecs","msg","name","pathname","process","processName",
                     "relativeCreated","stack_info","thread","threadName"):
                continue
            payload[k] = v
        return json.dumps(payload, ensure_ascii=False, default=str)


def _setup_logging():
    if os.environ.get("LOG_JSON", "true").lower() in ("0","false","no"):
        return
    root = logging.getLogger()
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    h = logging.StreamHandler(_sys.stdout)
    h.setFormatter(JsonFormatter())
    # Reemplazar handlers existentes
    root.handlers = [h]
    # Silenciar logs ruidosos del access log de waitress
    logging.getLogger("waitress.queue").setLevel(logging.WARNING)


_setup_logging()

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, abort, g, send_file
)
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from database import init_db, get_conn

try:
    import stt
except Exception:
    stt = None

import triage
import backup as _backup_mod
from io import BytesIO

try:
    import llm
except ImportError:
    llm = None

import alertas as _alertas_mod
import busqueda as _busqueda_mod
import fhir as _fhir_mod

app = Flask(__name__)

# === Secret key: obligatoria en producción ===
# En desarrollo se genera una temporal. En despliegue setear:
#   export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
_default_key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".secret_key")
_env_key = os.environ.get("SECRET_KEY")
if _env_key:
    app.secret_key = _env_key
else:
    # Genera y persiste una clave aleatoria local si no se setea env.
    # Evita el anti-patrón de hardcodear "dev-key" en código.
    os.makedirs(os.path.dirname(_default_key_path), exist_ok=True)
    if os.path.exists(_default_key_path):
        with open(_default_key_path, "rb") as f:
            app.secret_key = f.read()
    else:
        import secrets as _secrets
        key = _secrets.token_bytes(32)
        with open(_default_key_path, "wb") as f:
            f.write(key)
        os.chmod(_default_key_path, 0o600)
        app.secret_key = key

app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # 30 MB (audio + datos)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["WTF_CSRF_TIME_LIMIT"] = None  # mientras dura la sesión

csrf = CSRFProtect(app)

# Rate limiting (storage in-memory; en multi-replica usar Redis URL)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["300 per minute", "5000 per hour"],
    storage_uri=os.environ.get("LIMITER_STORAGE", "memory://"),
    strategy="fixed-window",
)


@app.context_processor
def inject_csrf():
    return {"csrf_token": generate_csrf}


def _csrf_exempt_blueprint():
    """Exime los endpoints FHIR (auth por API key) de CSRF.
    Llamar después de registrar las rutas."""
    for endpoint in ("fhir_post_observation",):
        view = app.view_functions.get(endpoint)
        if view:
            csrf.exempt(view)


# ---------- Utilidades ----------

def ahora_iso():
    return datetime.now().isoformat(timespec="seconds")


def fmt_dt(s):
    if not s:
        return ""
    try:
        return datetime.fromisoformat(s).strftime("%d-%m-%Y %H:%M")
    except Exception:
        return s


def fmt_hora(s):
    if not s:
        return ""
    try:
        return datetime.fromisoformat(s).strftime("%H:%M")
    except Exception:
        return s


def fmt_fecha(s):
    if not s:
        return ""
    try:
        return datetime.fromisoformat(s).strftime("%d-%m-%Y")
    except Exception:
        return s


def tiempo_transcurrido(s):
    if not s:
        return ""
    try:
        inicio = datetime.fromisoformat(s)
        delta = datetime.now() - inicio
        horas = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        if horas > 0:
            return f"{horas}h {mins}m"
        return f"{mins}m"
    except Exception:
        return ""


def turno_actual_por_hora():
    """Devuelve 'dia' o 'noche' según hora local."""
    h = datetime.now().time()
    return "dia" if dtime(8, 0) <= h < dtime(20, 0) else "noche"


# Rangos fisiológicos para signos vitales. Valores fuera de estos rangos casi
# siempre son errores de input (negativos, ceros por sensor, typos como temp=999).
# Si un caso real necesita ir más allá, ampliar acá explícitamente.
_SV_RANGOS = {
    "fc":      (20, 250),     # lpm — paro a taquicardia extrema
    "fr":      (4, 60),       # rpm — apnea a taquipnea severa
    "temp":    (28.0, 43.0),  # °C — hipotermia severa a hiperpirexia
    "sato2":   (30, 100),     # %
    "glasgow": (3, 15),       # escala definida estricta
    "hgt":     (10, 1000),    # mg/dL — hipoglicemia severa a CAD extrema
    "eva":     (0, 10),
}


def _clamp_sv(raw, campo, parser):
    """
    Convierte raw → tipo y rechaza si está fuera de rango fisiológico.
    Retorna None si el input es vacío, inválido, o fuera de rango.
    Llamar para fc/fr/sato2/glasgow/hgt/eva (int) y temp (float).
    """
    if raw is None or raw == "":
        return None
    try:
        v = parser(raw)
    except (TypeError, ValueError):
        return None
    lo, hi = _SV_RANGOS[campo]
    if v < lo or v > hi:
        return None
    return v


def _clamp_pa(raw):
    """
    Valida PA "sistólica/diastólica" (string). Retorna el string normalizado
    si ambos números están en rango, o None si no parsea o está fuera de rango.
    Acepta también solo sistólica.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    partes = s.split("/")
    try:
        sis = int("".join(c for c in partes[0] if c.isdigit()))
    except ValueError:
        return None
    if not (30 <= sis <= 260):
        return None
    if len(partes) >= 2 and partes[1].strip():
        try:
            dia = int("".join(c for c in partes[1] if c.isdigit()))
        except ValueError:
            return None
        if not (10 <= dia <= 200):
            return None
        return f"{sis}/{dia}"
    return str(sis)


_ip_local_cache: Optional[str] = None


def get_ip_local():
    """IP en LAN para mostrar al iniciar. Cacheada — no se ejecuta por request."""
    global _ip_local_cache
    if _ip_local_cache is not None:
        return _ip_local_cache
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.5)
        s.connect(("10.255.255.255", 1))
        _ip_local_cache = s.getsockname()[0]
    except Exception:
        _ip_local_cache = "127.0.0.1"
    finally:
        s.close()
    return _ip_local_cache


app.jinja_env.filters["fmt_dt"] = fmt_dt
app.jinja_env.filters["fmt_hora"] = fmt_hora
app.jinja_env.filters["fmt_fecha"] = fmt_fecha
app.jinja_env.filters["transcurrido"] = tiempo_transcurrido


# ---------- Auth & autorización ----------

def _paciente_modificable(conn, pid: int) -> bool:
    """
    Un paciente es modificable si:
      - Su turno es el activo, O
      - Está en estado 'en_atencion' (independiente del turno: handoff continuo)
    Esto previene IDOR: modificar pacientes ya egresados de turnos cerrados.
    """
    p = conn.execute(
        """SELECT p.turno_id, p.estado, t.estado AS estado_turno
           FROM pacientes p
           LEFT JOIN turnos t ON t.id = p.turno_id
           WHERE p.id = ?""",
        (pid,),
    ).fetchone()
    if not p:
        return False
    if p["estado"] == "en_atencion":
        return True
    if p["estado_turno"] == "activo":
        return True
    return False


def autorizado_o_404(conn, pid: int):
    """Aborta con 403 si el paciente no es modificable en el contexto actual."""
    if not _paciente_modificable(conn, pid):
        abort(403)


# ===== RBAC: control de acceso basado en roles =====
# Permisos por rol. Los roles definen capacidades, no jerarquía.
ROLE_PERMS = {
    "admin":  {"abrir_turno","cerrar_turno","crear_paciente","editar_paciente",
               "cambiar_estado","aceptar_esi","agregar_sv","agregar_nota","agregar_pendiente",
               "ver_auditoria","ver_alertas","gestionar_usuarios","exportar_pdf"},
    "medico": {"abrir_turno","cerrar_turno","crear_paciente","editar_paciente",
               "cambiar_estado","aceptar_esi","agregar_sv","agregar_nota","agregar_pendiente",
               "ver_alertas","exportar_pdf"},
    "eu":     {"crear_paciente","editar_paciente","cambiar_estado",
               "agregar_sv","agregar_nota","agregar_pendiente","ver_alertas","exportar_pdf"},
    "tens":   {"agregar_sv","agregar_nota","ver_alertas"},
}


def tiene_permiso(perm: str) -> bool:
    u = g.get("usuario")
    if not u: return False
    return perm in ROLE_PERMS.get(u["rol"], set())


def requiere_permiso(perm: str):
    """Decorator: aborta 403 si el usuario no tiene el permiso."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get("usuario_id"):
                return redirect(url_for("login", next=request.path))
            if not tiene_permiso(perm):
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return deco


def audit(conn, accion: str, recurso: str, recurso_id=None, detalle=None):
    """Registra una entrada en la tabla auditoria. Llamar antes de commit."""
    conn.execute(
        """INSERT INTO auditoria (actor_id, accion, recurso, recurso_id, detalle, ip, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            g.usuario["id"] if g.get("usuario") else None,
            accion, recurso, recurso_id,
            detalle if isinstance(detalle, str) or detalle is None else json.dumps(detalle, ensure_ascii=False),
            request.remote_addr if request else None,
            ahora_iso(),
        ),
    )




def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("usuario_id"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


@app.before_request
def cargar_contexto():
    g.usuario = None
    g.turno_activo = None
    if session.get("usuario_id"):
        conn = get_conn()
        g.usuario = conn.execute(
            "SELECT * FROM usuarios WHERE id=?", (session["usuario_id"],)
        ).fetchone()
        g.turno_activo = conn.execute(
            "SELECT * FROM turnos WHERE estado='activo' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()


def _asset_version():
    """mtime del CSS+JS para cache-busting al desplegar cambios."""
    base = os.path.dirname(os.path.abspath(__file__))
    paths = [
        os.path.join(base, "static", "css", "style.css"),
        os.path.join(base, "static", "js", "app.js"),
    ]
    try:
        return int(max(os.path.getmtime(p) for p in paths))
    except Exception:
        return 0


def _n_alertas_abiertas() -> int:
    if not g.get("usuario"):
        return 0
    try:
        conn = get_conn()
        n = conn.execute(
            """SELECT COUNT(*) AS n FROM alertas_sv a
               JOIN pacientes p ON p.id = a.paciente_id
               WHERE a.reconocida = 0 AND p.estado = 'en_atencion'"""
        ).fetchone()["n"]
        conn.close()
        return n
    except Exception:
        return 0


@app.context_processor
def inject_globals():
    return {
        "usuario": g.get("usuario"),
        "turno_activo": g.get("turno_activo"),
        "ip_local": get_ip_local(),
        "asset_v": _asset_version(),
        "tiene_permiso": tiene_permiso,
        "n_alertas_abiertas": _n_alertas_abiertas,
    }


# ---------- Rutas: Auth ----------

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    conn = get_conn()
    if request.method == "POST":
        uid = request.form.get("usuario_id")
        if not uid:
            flash("Selecciona un usuario", "error")
        else:
            session["usuario_id"] = int(uid)
            nxt = request.args.get("next") or url_for("dashboard")
            conn.close()
            return redirect(nxt)
    usuarios = conn.execute(
        "SELECT * FROM usuarios WHERE activo=1 ORDER BY rol, nombre"
    ).fetchall()
    conn.close()
    return render_template("login.html", usuarios=usuarios)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- Dashboard ----------

@app.route("/")
@login_required
def dashboard():
    conn = get_conn()
    turno = g.turno_activo

    stats = {
        "total": 0, "c1": 0, "c2": 0, "c3": 0, "c4": 0, "c5": 0,
        "en_atencion": 0, "alta": 0, "hospitalizado": 0,
        "pendientes_abiertos": 0,
    }
    pacientes = []
    if turno:
        # Pacientes del turno activo + pacientes recibidos (en atención
        # con turno_id de turnos anteriores → handoff entre turnos)
        pacientes = conn.execute(
            """SELECT p.*, (p.turno_id != ?) AS recibido
               FROM pacientes p
               WHERE p.turno_id = ?
                  OR (p.estado = 'en_atencion' AND p.turno_id != ?)
               ORDER BY recibido ASC,
                   CASE categoria_esi WHEN 'C1' THEN 1 WHEN 'C2' THEN 2
                                      WHEN 'C3' THEN 3 WHEN 'C4' THEN 4 ELSE 5 END,
                   ingreso DESC""",
            (turno["id"], turno["id"], turno["id"]),
        ).fetchall()
        stats["total"] = len(pacientes)
        for p in pacientes:
            stats[p["categoria_esi"].lower()] = stats.get(p["categoria_esi"].lower(), 0) + 1
            if p["estado"] == "en_atencion":
                stats["en_atencion"] += 1
            elif p["estado"] == "alta":
                stats["alta"] += 1
            elif p["estado"] == "hospitalizado":
                stats["hospitalizado"] += 1
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM pendientes
               WHERE estado IN ('pendiente','en_curso')
               AND paciente_id IN (SELECT id FROM pacientes WHERE turno_id=?)""",
            (turno["id"],),
        ).fetchone()
        stats["pendientes_abiertos"] = row["n"]

    conn.close()
    return render_template("dashboard.html", turno=turno, pacientes=pacientes, stats=stats)


# ---------- Turnos ----------

@app.route("/turno/nuevo", methods=["GET", "POST"])
@requiere_permiso("abrir_turno")
def turno_nuevo():
    conn = get_conn()
    if conn.execute("SELECT 1 FROM turnos WHERE estado='activo' LIMIT 1").fetchone():
        flash("Ya hay un turno activo. Ciérralo antes de abrir otro.", "error")
        conn.close()
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        tens_ids = request.form.getlist("tens_ids")
        cur = conn.execute(
            """INSERT INTO turnos (tipo, fecha_inicio, medico_jefe_id, eu_id, tens_ids, notas_apertura, estado)
               VALUES (?, ?, ?, ?, ?, ?, 'activo')""",
            (
                request.form["tipo"],
                ahora_iso(),
                request.form.get("medico_jefe_id") or None,
                request.form.get("eu_id") or None,
                json.dumps(tens_ids),
                request.form.get("notas_apertura", "").strip(),
            ),
        )
        nuevo_turno_id = cur.lastrowid

        # === HANDOFF: registrar nota automática a cada paciente en atención
        # que provenga de un turno previo. NO cambia su turno_id (trazabilidad).
        nombre_medico = conn.execute(
            "SELECT nombre FROM usuarios WHERE id=?",
            (request.form.get("medico_jefe_id"),),
        ).fetchone()
        nombre_medico = nombre_medico["nombre"] if nombre_medico else "(sin médico)"
        nombre_eu = conn.execute(
            "SELECT nombre FROM usuarios WHERE id=?",
            (request.form.get("eu_id"),),
        ).fetchone()
        nombre_eu = nombre_eu["nombre"] if nombre_eu else "(sin EU)"
        tipo_txt = "DÍA" if request.form["tipo"] == "dia" else "NOCHE"

        recibidos = conn.execute(
            """SELECT p.id, p.nombre, p.turno_id
               FROM pacientes p
               WHERE p.estado='en_atencion' AND p.turno_id != ?""",
            (nuevo_turno_id,),
        ).fetchall()

        for p in recibidos:
            conn.execute(
                "INSERT INTO notas (paciente_id, contenido, autor_id, creado_en) VALUES (?,?,?,?)",
                (
                    p["id"],
                    f"⇄ Paciente recibido por el turno {tipo_txt}. "
                    f"Médico jefe: {nombre_medico}. EU responsable: {nombre_eu}. "
                    f"Continúa en atención desde turno anterior.",
                    g.usuario["id"],
                    ahora_iso(),
                ),
            )

        conn.commit()
        conn.close()
        if recibidos:
            flash(
                f"Turno abierto. Se recibieron {len(recibidos)} paciente(s) "
                f"en atención del turno anterior.",
                "ok",
            )
        else:
            flash("Turno abierto correctamente", "ok")
        return redirect(url_for("dashboard"))

    medicos = conn.execute(
        "SELECT * FROM usuarios WHERE rol='medico' AND activo=1 ORDER BY nombre"
    ).fetchall()
    eus = conn.execute(
        "SELECT * FROM usuarios WHERE rol='eu' AND activo=1 ORDER BY nombre"
    ).fetchall()
    tens = conn.execute(
        "SELECT * FROM usuarios WHERE rol='tens' AND activo=1 ORDER BY nombre"
    ).fetchall()
    conn.close()
    return render_template(
        "turno_nuevo.html",
        medicos=medicos, eus=eus, tens=tens,
        tipo_sugerido=turno_actual_por_hora(),
    )


@app.route("/turno/<int:turno_id>/cerrar", methods=["GET", "POST"])
@requiere_permiso("cerrar_turno")
def turno_cerrar(turno_id):
    conn = get_conn()
    turno = conn.execute("SELECT * FROM turnos WHERE id=?", (turno_id,)).fetchone()
    if not turno:
        conn.close()
        abort(404)
    if turno["estado"] != "activo":
        conn.close()
        flash("El turno ya está cerrado", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        conn.execute(
            "UPDATE turnos SET estado='cerrado', fecha_cierre=?, notas_cierre=? WHERE id=?",
            (ahora_iso(), request.form.get("notas_cierre", "").strip(), turno_id),
        )
        audit(conn, "cerrar_turno", "turno", turno_id)
        conn.commit()
        conn.close()
        # Backup automático tras cierre de turno (no-bloqueante)
        try:
            _backup_mod.do_backup()
        except Exception as e:
            app.logger.warning(f"backup falló: {e}")
        flash("Turno cerrado. Entrega disponible en historial.", "ok")
        return redirect(url_for("entrega_turno", turno_id=turno_id))

    pacientes_activos = conn.execute(
        "SELECT COUNT(*) AS n FROM pacientes WHERE turno_id=? AND estado='en_atencion'",
        (turno_id,),
    ).fetchone()["n"]
    pendientes = conn.execute(
        """SELECT COUNT(*) AS n FROM pendientes
           WHERE estado IN ('pendiente','en_curso')
           AND paciente_id IN (SELECT id FROM pacientes WHERE turno_id=?)""",
        (turno_id,),
    ).fetchone()["n"]
    conn.close()
    return render_template(
        "turno_cerrar.html",
        turno=turno,
        pacientes_activos=pacientes_activos,
        pendientes=pendientes,
    )


# ---------- Pacientes ----------

@app.route("/paciente/nuevo", methods=["GET", "POST"])
@requiere_permiso("crear_paciente")
def paciente_nuevo():
    if not g.turno_activo:
        flash("No hay turno activo. Abre un turno primero.", "error")
        return redirect(url_for("turno_nuevo"))

    if request.method == "POST":
        f = request.form
        # Sanitizar SV antes de pasar al triage y persistir.
        pa = _clamp_pa(f.get("pa"))
        fc = _clamp_sv(f.get("fc"), "fc", int)
        fr = _clamp_sv(f.get("fr"), "fr", int)
        temp = _clamp_sv(f.get("temp"), "temp", float)
        sato2 = _clamp_sv(f.get("sato2"), "sato2", int)
        glasgow = _clamp_sv(f.get("glasgow"), "glasgow", int)
        hgt = _clamp_sv(f.get("hgt"), "hgt", int)
        edad = None
        if f.get("edad"):
            try:
                e = int(f["edad"])
                edad = e if 0 <= e <= 130 else None
            except ValueError:
                edad = None
        # Calcular sugerencia ESI con los datos saneados
        sug_cat, sug_razones = triage.sugerir_categoria({
            "motivo_consulta": f.get("motivo_consulta", ""),
            "antecedentes":    f.get("antecedentes", ""),
            "edad":            edad,
            "pa":              pa or "",
            "fc":              fc,
            "fr":              fr,
            "temp":            temp,
            "sato2":           sato2,
            "glasgow":         glasgow,
        })
        conn = get_conn()
        cur = conn.execute(
            """INSERT INTO pacientes
               (turno_id, nombre, rut, edad, sexo, categoria_esi, box,
                motivo_consulta, antecedentes, alergias,
                pa, fc, fr, temp, sato2, glasgow, hgt,
                estado, ingreso, creado_por, esi_sugerido, esi_razones)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'en_atencion', ?, ?, ?, ?)""",
            (
                g.turno_activo["id"],
                f["nombre"].strip(),
                f.get("rut", "").strip() or None,
                edad,
                f.get("sexo") or None,
                f["categoria_esi"],
                f.get("box", "").strip() or None,
                f.get("motivo_consulta", "").strip() or None,
                f.get("antecedentes", "").strip() or None,
                f.get("alergias", "").strip() or None,
                pa,
                fc,
                fr,
                temp,
                sato2,
                glasgow,
                hgt,
                ahora_iso(),
                g.usuario["id"],
                sug_cat,
                json.dumps(sug_razones, ensure_ascii=False),
            ),
        )
        pid = cur.lastrowid
        # registrar primera toma de SV
        if any(v is not None for v in (pa, fc, fr, temp, sato2, glasgow, hgt)):
            conn.execute(
                """INSERT INTO signos_vitales
                   (paciente_id, pa, fc, fr, temp, sato2, glasgow, hgt, autor_id, creado_en)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (pid, pa, fc, fr, temp, sato2, glasgow, hgt, g.usuario["id"], ahora_iso()),
            )
        conn.commit()
        conn.close()
        if f.get("guardar_y_otro"):
            flash(f"Paciente «{f['nombre'].strip()}» registrado. Agrega el siguiente.", "ok")
            return redirect(url_for("paciente_nuevo"))
        flash("Paciente registrado", "ok")
        return redirect(url_for("paciente_detalle", pid=pid))

    return render_template("paciente_nuevo.html")


@app.route("/paciente/<int:pid>")
@login_required
def paciente_detalle(pid):
    conn = get_conn()
    p = conn.execute(
        """SELECT p.*, u.nombre AS creado_por_nombre
           FROM pacientes p LEFT JOIN usuarios u ON u.id=p.creado_por
           WHERE p.id=?""",
        (pid,),
    ).fetchone()
    if not p:
        conn.close()
        abort(404)
    notas = conn.execute(
        """SELECT n.*, u.nombre AS autor_nombre, u.rol AS autor_rol
           FROM notas n LEFT JOIN usuarios u ON u.id=n.autor_id
           WHERE n.paciente_id=? ORDER BY n.creado_en DESC""",
        (pid,),
    ).fetchall()
    pendientes = conn.execute(
        """SELECT * FROM pendientes WHERE paciente_id=?
           ORDER BY CASE estado WHEN 'pendiente' THEN 1 WHEN 'en_curso' THEN 2 ELSE 3 END,
                    creado_en DESC""",
        (pid,),
    ).fetchall()
    signos = conn.execute(
        """SELECT sv.*, u.nombre AS autor_nombre
           FROM signos_vitales sv LEFT JOIN usuarios u ON u.id=sv.autor_id
           WHERE sv.paciente_id=? ORDER BY sv.creado_en DESC""",
        (pid,),
    ).fetchall()
    # Razones de la sugerencia ESI (JSON → lista)
    razones = []
    if p["esi_razones"]:
        try:
            razones = json.loads(p["esi_razones"])
        except Exception:
            razones = [p["esi_razones"]]
    # Alertas no reconocidas
    alertas_abiertas = conn.execute(
        """SELECT * FROM alertas_sv WHERE paciente_id=? AND reconocida=0
           ORDER BY id DESC""",
        (pid,),
    ).fetchall()
    conn.close()
    return render_template(
        "paciente_detalle.html",
        p=p, notas=notas, pendientes=pendientes, signos=signos,
        esi_razones=razones, alertas_abiertas=alertas_abiertas,
    )


@app.route("/paciente/<int:pid>/editar", methods=["GET", "POST"])
@login_required
def paciente_editar(pid):
    conn = get_conn()
    p = conn.execute("SELECT * FROM pacientes WHERE id=?", (pid,)).fetchone()
    if not p:
        conn.close()
        abort(404)
    if request.method == "POST":
        f = request.form
        conn.execute(
            """UPDATE pacientes SET nombre=?, rut=?, edad=?, sexo=?, categoria_esi=?,
                                    box=?, motivo_consulta=?, antecedentes=?, alergias=?
               WHERE id=?""",
            (
                f["nombre"].strip(),
                f.get("rut", "").strip() or None,
                int(f["edad"]) if f.get("edad") else None,
                f.get("sexo") or None,
                f["categoria_esi"],
                f.get("box", "").strip() or None,
                f.get("motivo_consulta", "").strip() or None,
                f.get("antecedentes", "").strip() or None,
                f.get("alergias", "").strip() or None,
                pid,
            ),
        )
        conn.commit()
        conn.close()
        flash("Paciente actualizado", "ok")
        return redirect(url_for("paciente_detalle", pid=pid))
    conn.close()
    return render_template("paciente_editar.html", p=p)


@app.route("/paciente/<int:pid>/aceptar-esi", methods=["POST"])
@login_required
def paciente_aceptar_esi(pid):
    """Aplica la categoría sugerida por el sistema."""
    conn = get_conn()
    p = conn.execute("SELECT esi_sugerido, categoria_esi FROM pacientes WHERE id=?", (pid,)).fetchone()
    if not p:
        conn.close()
        abort(404)
    if not p["esi_sugerido"]:
        conn.close()
        flash("Sin sugerencia disponible para este paciente.", "error")
        return redirect(url_for("paciente_detalle", pid=pid))
    if p["esi_sugerido"] == p["categoria_esi"]:
        conn.close()
        flash("La categoría actual ya coincide con la sugerida.", "ok")
        return redirect(url_for("paciente_detalle", pid=pid))
    anterior = p["categoria_esi"]
    conn.execute("UPDATE pacientes SET categoria_esi=? WHERE id=?", (p["esi_sugerido"], pid))
    audit(conn, "aceptar_esi", "paciente", pid, {"de": anterior, "a": p["esi_sugerido"]})
    conn.execute(
        "INSERT INTO notas (paciente_id, contenido, autor_id, creado_en) VALUES (?,?,?,?)",
        (
            pid,
            f"⓵ Categorización ESI actualizada de {anterior} → {p['esi_sugerido']} "
            f"(aceptando sugerencia del sistema).",
            g.usuario["id"],
            ahora_iso(),
        ),
    )
    conn.commit()
    conn.close()
    flash(f"Categoría actualizada a {p['esi_sugerido']}.", "ok")
    return redirect(url_for("paciente_detalle", pid=pid))


@app.route("/paciente/<int:pid>/estado", methods=["POST"])
@login_required
def paciente_cambiar_estado(pid):
    nuevo = request.form.get("estado")
    if nuevo not in ("en_atencion", "alta", "hospitalizado", "traslado", "fallecido", "fugado"):
        abort(400)
    conn = get_conn()
    autorizado_o_404(conn, pid)
    egreso = None if nuevo == "en_atencion" else ahora_iso()
    if nuevo == "en_atencion":
        conn.execute("UPDATE pacientes SET estado=?, egreso=NULL WHERE id=?", (nuevo, pid))
    else:
        conn.execute("UPDATE pacientes SET estado=?, egreso=? WHERE id=?", (nuevo, egreso, pid))
    audit(conn, "cambiar_estado", "paciente", pid, {"nuevo": nuevo})
    conn.commit()
    conn.close()
    flash(f"Estado actualizado: {nuevo.replace('_',' ')}", "ok")
    return redirect(url_for("paciente_detalle", pid=pid))


# ---------- Signos vitales ----------

@app.route("/paciente/<int:pid>/sv", methods=["POST"])
@login_required
def signos_agregar(pid):
    f = request.form
    conn = get_conn()
    autorizado_o_404(conn, pid)
    # Sanitizar y validar contra rangos fisiológicos antes de persistir.
    pa = _clamp_pa(f.get("pa"))
    fc = _clamp_sv(f.get("fc"), "fc", int)
    fr = _clamp_sv(f.get("fr"), "fr", int)
    temp = _clamp_sv(f.get("temp"), "temp", float)
    sato2 = _clamp_sv(f.get("sato2"), "sato2", int)
    glasgow = _clamp_sv(f.get("glasgow"), "glasgow", int)
    hgt = _clamp_sv(f.get("hgt"), "hgt", int)
    eva = _clamp_sv(f.get("eva"), "eva", int)
    # Si el usuario mandó algo pero todo se filtró, avisar.
    enviados = [k for k in ("pa", "fc", "fr", "temp", "sato2", "glasgow", "hgt", "eva") if f.get(k)]
    aceptados = [v for v in (pa, fc, fr, temp, sato2, glasgow, hgt, eva) if v is not None]
    if enviados and not aceptados:
        conn.close()
        flash("Valores fuera de rango fisiológico. Revisar y reintentar.", "error")
        return redirect(url_for("paciente_detalle", pid=pid))
    conn.execute(
        """INSERT INTO signos_vitales
           (paciente_id, pa, fc, fr, temp, sato2, glasgow, hgt, eva, autor_id, creado_en)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (pid, pa, fc, fr, temp, sato2, glasgow, hgt, eva, g.usuario["id"], ahora_iso()),
    )
    # también actualizar SV "actuales" en paciente
    conn.execute(
        """UPDATE pacientes SET pa=COALESCE(?,pa), fc=COALESCE(?,fc), fr=COALESCE(?,fr),
                                temp=COALESCE(?,temp), sato2=COALESCE(?,sato2),
                                glasgow=COALESCE(?,glasgow), hgt=COALESCE(?,hgt)
           WHERE id=?""",
        (pa, fc, fr, temp, sato2, glasgow, hgt, pid),
    )
    # Evaluar alertas tras la nueva toma de SV
    try:
        ahora = ahora_iso()
        nuevas_alertas = _alertas_mod.evaluar_paciente(conn, pid)
        if nuevas_alertas:
            n = _alertas_mod.registrar_alertas(conn, pid, nuevas_alertas, ahora)
            if n:
                tipos = ", ".join(a["tipo"] for a in nuevas_alertas)
                audit(conn, "alerta_generada", "paciente", pid, {"alertas": tipos})
                flash(f"⚠ {n} alerta(s) generada(s): {tipos}", "error")
    except Exception as e:
        app.logger.warning(f"alertas SV fallaron: {e}")
    conn.commit()
    conn.close()
    return redirect(url_for("paciente_detalle", pid=pid))


@app.route("/alerta/<int:aid>/reconocer", methods=["POST"])
@requiere_permiso("ver_alertas")
def alerta_reconocer(aid):
    conn = get_conn()
    a = conn.execute("SELECT paciente_id FROM alertas_sv WHERE id=?", (aid,)).fetchone()
    if not a:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE alertas_sv SET reconocida=1, reconocida_por=?, reconocida_en=? WHERE id=?",
        (g.usuario["id"], ahora_iso(), aid),
    )
    audit(conn, "reconocer_alerta", "alerta", aid)
    conn.commit()
    conn.close()
    return redirect(url_for("paciente_detalle", pid=a["paciente_id"]))


# ---------- Notas ----------

@app.route("/paciente/<int:pid>/nota", methods=["POST"])
@login_required
def nota_agregar(pid):
    contenido = request.form.get("contenido", "").strip()
    if not contenido:
        flash("La nota no puede estar vacía", "error")
        return redirect(url_for("paciente_detalle", pid=pid))
    conn = get_conn()
    autorizado_o_404(conn, pid)
    cur = conn.execute(
        "INSERT INTO notas (paciente_id, contenido, autor_id, creado_en) VALUES (?,?,?,?)",
        (pid, contenido, g.usuario["id"], ahora_iso()),
    )
    nota_id = cur.lastrowid
    conn.commit()
    conn.close()
    # Indexar incrementalmente para que la nota aparezca en /buscar sin requerir
    # un reindex manual. Falla silenciosamente (la nota ya está persistida).
    try:
        _busqueda_mod.index_nota(nota_id, contenido)
    except Exception as e:
        app.logger.warning(f"index nota falló: {e}")
    return redirect(url_for("paciente_detalle", pid=pid))


# ---------- Pendientes ----------

@app.route("/paciente/<int:pid>/pendiente", methods=["POST"])
@login_required
def pendiente_agregar(pid):
    f = request.form
    descripcion = f.get("descripcion", "").strip()
    if not descripcion:
        flash("Descripción requerida", "error")
        return redirect(url_for("paciente_detalle", pid=pid))
    conn = get_conn()
    autorizado_o_404(conn, pid)
    conn.execute(
        """INSERT INTO pendientes (paciente_id, tipo, descripcion, autor_id, creado_en)
           VALUES (?,?,?,?,?)""",
        (pid, f.get("tipo", "otro"), descripcion, g.usuario["id"], ahora_iso()),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("paciente_detalle", pid=pid))


@app.route("/pendiente/<int:peid>/estado", methods=["POST"])
@login_required
def pendiente_cambiar(peid):
    nuevo = request.form.get("estado")
    if nuevo not in ("pendiente", "en_curso", "completado", "cancelado"):
        abort(400)
    conn = get_conn()
    completado = ahora_iso() if nuevo == "completado" else None
    conn.execute(
        "UPDATE pendientes SET estado=?, completado_en=? WHERE id=?",
        (nuevo, completado, peid),
    )
    row = conn.execute("SELECT paciente_id FROM pendientes WHERE id=?", (peid,)).fetchone()
    conn.commit()
    conn.close()
    if row:
        return redirect(url_for("paciente_detalle", pid=row["paciente_id"]))
    return redirect(url_for("dashboard"))


# ---------- Entrega de turno (resumen imprimible) ----------

@app.route("/turno/<int:turno_id>/entrega")
@login_required
def entrega_turno(turno_id):
    conn = get_conn()
    turno = conn.execute("SELECT * FROM turnos WHERE id=?", (turno_id,)).fetchone()
    if not turno:
        conn.close()
        abort(404)

    medico = conn.execute(
        "SELECT * FROM usuarios WHERE id=?", (turno["medico_jefe_id"],)
    ).fetchone() if turno["medico_jefe_id"] else None
    eu = conn.execute(
        "SELECT * FROM usuarios WHERE id=?", (turno["eu_id"],)
    ).fetchone() if turno["eu_id"] else None
    tens_ids = json.loads(turno["tens_ids"] or "[]")
    tens_list = []
    if tens_ids:
        q_marks = ",".join("?" * len(tens_ids))
        tens_list = conn.execute(
            f"SELECT * FROM usuarios WHERE id IN ({q_marks})", tens_ids
        ).fetchall()

    # Pacientes del turno + (si está activo) pacientes recibidos del turno previo
    if turno["estado"] == "activo":
        pacientes = conn.execute(
            """SELECT p.*, (p.turno_id != ?) AS recibido FROM pacientes p
               WHERE p.turno_id = ?
                  OR (p.estado='en_atencion' AND p.turno_id != ?)
               ORDER BY recibido ASC,
                        CASE p.estado WHEN 'en_atencion' THEN 1 ELSE 2 END,
                        CASE p.categoria_esi WHEN 'C1' THEN 1 WHEN 'C2' THEN 2
                                              WHEN 'C3' THEN 3 WHEN 'C4' THEN 4 ELSE 5 END,
                        p.ingreso""",
            (turno_id, turno_id, turno_id),
        ).fetchall()
    else:
        pacientes = conn.execute(
            """SELECT *, 0 AS recibido FROM pacientes WHERE turno_id=?
               ORDER BY CASE estado WHEN 'en_atencion' THEN 1 ELSE 2 END,
                        CASE categoria_esi WHEN 'C1' THEN 1 WHEN 'C2' THEN 2
                                            WHEN 'C3' THEN 3 WHEN 'C4' THEN 4 ELSE 5 END,
                        ingreso""",
            (turno_id,),
        ).fetchall()

    # Para cada paciente: últimas notas, pendientes abiertos, últimos SV
    detalle = {}
    for p in pacientes:
        notas = conn.execute(
            """SELECT n.*, u.nombre AS autor_nombre, u.rol AS autor_rol
               FROM notas n LEFT JOIN usuarios u ON u.id=n.autor_id
               WHERE n.paciente_id=? ORDER BY n.creado_en DESC LIMIT 3""",
            (p["id"],),
        ).fetchall()
        pendientes = conn.execute(
            """SELECT * FROM pendientes WHERE paciente_id=?
               AND estado IN ('pendiente','en_curso') ORDER BY creado_en""",
            (p["id"],),
        ).fetchall()
        sv_ult = conn.execute(
            """SELECT * FROM signos_vitales WHERE paciente_id=?
               ORDER BY creado_en DESC LIMIT 1""",
            (p["id"],),
        ).fetchone()
        detalle[p["id"]] = {"notas": notas, "pendientes": pendientes, "sv": sv_ult}

    # estadísticas
    stats = {"total": len(pacientes), "c1": 0, "c2": 0, "c3": 0, "c4": 0, "c5": 0,
             "alta": 0, "hosp": 0, "traslado": 0, "fallecido": 0, "en_atencion": 0}
    for p in pacientes:
        stats[p["categoria_esi"].lower()] += 1
        if p["estado"] == "alta": stats["alta"] += 1
        elif p["estado"] == "hospitalizado": stats["hosp"] += 1
        elif p["estado"] == "traslado": stats["traslado"] += 1
        elif p["estado"] == "fallecido": stats["fallecido"] += 1
        elif p["estado"] == "en_atencion": stats["en_atencion"] += 1

    conn.close()
    return render_template(
        "entrega.html",
        turno=turno, medico=medico, eu=eu, tens_list=tens_list,
        pacientes=pacientes, detalle=detalle, stats=stats,
        generado_en=ahora_iso(),
    )


@app.route("/turno/<int:turno_id>/entrega.pdf")
@login_required
def entrega_turno_pdf(turno_id):
    """Genera la entrega de turno como PDF usando WeasyPrint."""
    try:
        from weasyprint import HTML
    except ImportError:
        flash("WeasyPrint no instalado. Instalar con: pip install weasyprint", "error")
        return redirect(url_for("entrega_turno", turno_id=turno_id))

    # Renderizamos el HTML llamando a la vista internamente
    with app.test_request_context():
        # En vez de duplicar lógica, hacemos un fetch interno
        pass
    # Simplemente reutilizamos render usando un cliente interno
    with app.test_client() as client:
        with client.session_transaction() as sess:
            if g.get("usuario"):
                sess["usuario_id"] = g.usuario["id"]
        resp = client.get(f"/turno/{turno_id}/entrega")
        if resp.status_code != 200:
            abort(404)
        html_str = resp.data.decode("utf-8")

    base_url = request.url_root
    pdf_bytes = HTML(string=html_str, base_url=base_url).write_pdf()
    audit_conn = get_conn()
    audit(audit_conn, "export_pdf_entrega", "turno", turno_id)
    audit_conn.commit()
    audit_conn.close()
    filename = f"entrega_turno_{turno_id}.pdf"
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


# ---------- Historial ----------

@app.route("/historial")
@login_required
def historial():
    conn = get_conn()
    turnos = conn.execute(
        """SELECT t.*,
                  (SELECT COUNT(*) FROM pacientes WHERE turno_id=t.id) AS n_pacientes,
                  mj.nombre AS medico_nombre, eu.nombre AS eu_nombre
           FROM turnos t
           LEFT JOIN usuarios mj ON mj.id=t.medico_jefe_id
           LEFT JOIN usuarios eu ON eu.id=t.eu_id
           WHERE t.estado='cerrado'
           ORDER BY t.fecha_cierre DESC LIMIT 100"""
    ).fetchall()
    conn.close()
    return render_template("historial.html", turnos=turnos)


# ---------- Usuarios (admin básico) ----------

@app.route("/usuarios", methods=["GET", "POST"])
@requiere_permiso("gestionar_usuarios")
def usuarios():
    conn = get_conn()
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        rol = request.form.get("rol")
        rut = request.form.get("rut", "").strip() or None
        if nombre and rol in ("medico", "eu", "tens", "admin"):
            try:
                conn.execute(
                    "INSERT INTO usuarios (nombre, rol, rut, activo, creado_en) VALUES (?,?,?,1,?)",
                    (nombre, rol, rut, ahora_iso()),
                )
                conn.commit()
                flash("Usuario creado", "ok")
            except Exception as e:
                flash(f"Error: {e}", "error")
        else:
            flash("Datos inválidos", "error")
    lista = conn.execute("SELECT * FROM usuarios ORDER BY activo DESC, rol, nombre").fetchall()
    conn.close()
    return render_template("usuarios.html", usuarios=lista)


@app.route("/buscar")
@login_required
def buscar():
    """Búsqueda semántica de notas clínicas históricas."""
    q = request.args.get("q", "").strip()
    resultados = []
    if q:
        resultados = _busqueda_mod.buscar(q, limit=30)
    return render_template("buscar.html", q=q, resultados=resultados)


@app.route("/buscar/reindex", methods=["POST"])
@requiere_permiso("gestionar_usuarios")
def buscar_reindex():
    info = _busqueda_mod.rebuild_index()
    flash(f"Índice reconstruido: {info['n']} notas, {info.get('vocab',0)} términos", "ok")
    return redirect(url_for("buscar"))


@app.route("/alertas")
@requiere_permiso("ver_alertas")
def alertas_dashboard():
    """Dashboard global de alertas SV del turno activo."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT a.*, p.nombre AS paciente_nombre, p.categoria_esi, p.box,
                  p.estado AS paciente_estado
           FROM alertas_sv a
           JOIN pacientes p ON p.id = a.paciente_id
           WHERE a.reconocida = 0 AND p.estado = 'en_atencion'
           ORDER BY
             CASE a.severidad WHEN 'critico' THEN 1 WHEN 'warn' THEN 2 ELSE 3 END,
             a.id DESC"""
    ).fetchall()
    conn.close()
    return render_template("alertas.html", alertas=rows)


@app.route("/auditoria")
@requiere_permiso("ver_auditoria")
def auditoria():
    """Vista de auditoría — solo admins."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT a.*, u.nombre AS actor_nombre, u.rol AS actor_rol
           FROM auditoria a LEFT JOIN usuarios u ON u.id=a.actor_id
           ORDER BY a.id DESC LIMIT 200"""
    ).fetchall()
    conn.close()
    return render_template("auditoria.html", entradas=rows)


@app.route("/usuario/<int:uid>/toggle", methods=["POST"])
@requiere_permiso("gestionar_usuarios")
def usuario_toggle(uid):
    conn = get_conn()
    conn.execute("UPDATE usuarios SET activo = 1 - activo WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return redirect(url_for("usuarios"))


# ---------- API stub para integración futura HL7 / SEIS ----------

@app.route("/api/turnos/activo")
@login_required
def api_turno_activo():
    conn = get_conn()
    t = conn.execute("SELECT * FROM turnos WHERE estado='activo' LIMIT 1").fetchone()
    conn.close()
    return jsonify(dict(t) if t else {})


@app.route("/api/pacientes/turno/<int:turno_id>")
@login_required
def api_pacientes_turno(turno_id):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM pacientes WHERE turno_id=?", (turno_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/paciente/<int:pid>")
@login_required
def api_paciente(pid):
    conn = get_conn()
    p = conn.execute("SELECT * FROM pacientes WHERE id=?", (pid,)).fetchone()
    if not p:
        conn.close()
        return jsonify({"error": "no encontrado"}), 404
    notas = conn.execute("SELECT * FROM notas WHERE paciente_id=? ORDER BY creado_en", (pid,)).fetchall()
    pend = conn.execute("SELECT * FROM pendientes WHERE paciente_id=? ORDER BY creado_en", (pid,)).fetchall()
    sv = conn.execute("SELECT * FROM signos_vitales WHERE paciente_id=? ORDER BY creado_en", (pid,)).fetchall()
    conn.close()
    return jsonify({
        "paciente": dict(p),
        "notas": [dict(n) for n in notas],
        "pendientes": [dict(x) for x in pend],
        "signos_vitales": [dict(x) for x in sv],
    })


@app.route("/healthz")
@limiter.exempt
def healthz():
    """Healthcheck minimal sin auth (para Kubernetes/EasyPanel).
    NO expone conteos ni info clínica. Para detalles usar /api/admin/status."""
    try:
        conn = get_conn()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        return jsonify({"ok": True, "version": "1.0.0"})
    except Exception as e:
        return jsonify({"ok": False, "error": "db unavailable"}), 500


@app.route("/api/admin/status")
@requiere_permiso("ver_auditoria")
def api_admin_status():
    """Status detallado del sistema. Solo admin."""
    conn = get_conn()
    n_users = conn.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()["n"]
    n_pac = conn.execute("SELECT COUNT(*) AS n FROM pacientes").fetchone()["n"]
    n_turnos = conn.execute("SELECT COUNT(*) AS n FROM turnos").fetchone()["n"]
    t_activo = conn.execute("SELECT id FROM turnos WHERE estado='activo' LIMIT 1").fetchone()
    n_alertas = conn.execute(
        "SELECT COUNT(*) AS n FROM alertas_sv WHERE reconocida=0"
    ).fetchone()["n"]
    n_auditoria = conn.execute("SELECT COUNT(*) AS n FROM auditoria").fetchone()["n"]
    conn.close()
    return jsonify({
        "ok": True,
        "version": "1.0.0",
        "db": {
            "usuarios": n_users, "pacientes": n_pac, "turnos": n_turnos,
            "turno_activo_id": t_activo["id"] if t_activo else None,
            "alertas_abiertas": n_alertas,
            "auditoria_entradas": n_auditoria,
        },
        "stt": stt.status() if stt else {"available": False},
        "llm": llm.status() if llm else {"available": False},
        "ts": ahora_iso(),
    })


@app.route("/api/sugerir-esi", methods=["POST"])
@login_required
@limiter.limit("60 per minute")
def api_sugerir_esi():
    """Devuelve la categoría ESI sugerida + razones para los datos enviados."""
    data = request.get_json(silent=True) or request.form
    payload = {
        "motivo_consulta": data.get("motivo_consulta") or "",
        "antecedentes":    data.get("antecedentes") or "",
        "edad":            None,
        "pa":              _clamp_pa(data.get("pa")) or "",
        "fc":              _clamp_sv(data.get("fc"), "fc", int),
        "fr":              _clamp_sv(data.get("fr"), "fr", int),
        "temp":            _clamp_sv(data.get("temp"), "temp", float),
        "sato2":           _clamp_sv(data.get("sato2"), "sato2", int),
        "glasgow":         _clamp_sv(data.get("glasgow"), "glasgow", int),
    }
    # edad fuera de rangos SV (puede ser 0-130)
    if data.get("edad") not in (None, "", "null"):
        try:
            e = int(data["edad"])
            payload["edad"] = e if 0 <= e <= 130 else None
        except (TypeError, ValueError):
            payload["edad"] = None
    cat, razones = triage.sugerir_categoria(payload)
    return jsonify({
        "categoria": cat,
        "descripcion": triage.descripcion_categoria(cat),
        "razones": razones,
    })


@app.route("/api/stt/status")
def api_stt_status():
    if stt is None:
        return jsonify({"available": False, "error": "módulo stt no cargado"})
    return jsonify(stt.status())


@app.route("/api/transcribir", methods=["POST"])
@login_required
@limiter.limit("20 per minute; 200 per hour")
def api_transcribir():
    if stt is None or not stt.available():
        return jsonify({"error": "STT no disponible (faster-whisper no instalado)"}), 503
    if "audio" not in request.files:
        return jsonify({"error": "audio requerido"}), 400
    archivo = request.files["audio"]
    contexto = request.form.get("contexto", "general")
    nombre = (archivo.filename or "").lower()
    suffix = ".webm"
    for ext in (".webm", ".mp4", ".m4a", ".wav", ".ogg", ".mp3"):
        if nombre.endswith(ext):
            suffix = ext
            break
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        archivo.save(tmp.name)
        tmp.close()
        resultado = stt.transcribir(tmp.name, contexto=contexto)
        if "error" in resultado:
            return jsonify(resultado), 500
        return jsonify(resultado)
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


@app.route("/api/llm/status")
@login_required
def api_llm_status():
    return jsonify(llm.status() if llm else {"available": False, "error": "módulo llm no cargado"})


@app.route("/api/llm/resumen-turno/<int:turno_id>", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def api_llm_resumen(turno_id):
    """Genera resumen narrativo de la entrega del turno usando Claude."""
    if not llm or not llm.available():
        return jsonify({"error": "LLM no disponible (setear ANTHROPIC_API_KEY)"}), 503
    conn = get_conn()
    t = conn.execute("SELECT * FROM turnos WHERE id=?", (turno_id,)).fetchone()
    if not t:
        conn.close()
        abort(404)
    mj = conn.execute("SELECT nombre FROM usuarios WHERE id=?", (t["medico_jefe_id"],)).fetchone()
    eu = conn.execute("SELECT nombre FROM usuarios WHERE id=?", (t["eu_id"],)).fetchone()
    pacientes = conn.execute(
        """SELECT * FROM pacientes WHERE turno_id=? OR (estado='en_atencion' AND turno_id != ?)""",
        (turno_id, turno_id),
    ).fetchall()
    pac_data = []
    for p in pacientes:
        sv = conn.execute(
            "SELECT * FROM signos_vitales WHERE paciente_id=? ORDER BY creado_en DESC LIMIT 1",
            (p["id"],),
        ).fetchone()
        pendientes = conn.execute(
            "SELECT * FROM pendientes WHERE paciente_id=? AND estado IN ('pendiente','en_curso')",
            (p["id"],),
        ).fetchall()
        d = dict(p)
        d["sv_ultima"] = dict(sv) if sv else None
        d["pendientes"] = [dict(x) for x in pendientes]
        pac_data.append(d)
    conn.close()

    resultado = llm.resumen_entrega_turno({
        "turno_tipo": t["tipo"],
        "medico_saliente": mj["nombre"] if mj else "",
        "eu_saliente": eu["nombre"] if eu else "",
        "pacientes": pac_data,
    })
    audit_conn = get_conn()
    audit(audit_conn, "llm_resumen", "turno", turno_id,
          {"tokens_in": resultado.get("tokens_in"),
           "tokens_out": resultado.get("tokens_out")})
    audit_conn.commit()
    audit_conn.close()
    return jsonify(resultado)


@app.route("/api/llm/triage/<int:pid>", methods=["POST"])
@login_required
@limiter.limit("30 per hour")
def api_llm_triage(pid):
    """Segunda opinión LLM sobre la categorización ESI de un paciente."""
    if not llm or not llm.available():
        return jsonify({"error": "LLM no disponible"}), 503
    conn = get_conn()
    p = conn.execute("SELECT * FROM pacientes WHERE id=?", (pid,)).fetchone()
    if not p:
        conn.close()
        abort(404)
    razones = []
    if p["esi_razones"]:
        try: razones = json.loads(p["esi_razones"])
        except Exception: razones = []
    conn.close()
    resultado = llm.triage_complementario(dict(p), p["esi_sugerido"] or p["categoria_esi"], razones)
    return jsonify(resultado)


@app.route("/api/seis/ingreso", methods=["POST"])
@login_required
def api_seis_ingreso():
    """
    Recibe ingreso de paciente desde SEIS vía FHIR Patient resource.
    Esperado: body JSON con resourceType="Patient".
    """
    if not g.get("turno_activo"):
        return jsonify({"error": "sin turno activo"}), 409
    body = request.get_json(silent=True) or {}
    try:
        datos = _fhir_mod.fhir_patient_to_paciente(body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    motivo = body.get("_motivo_consulta", "Derivado desde SEIS")
    categoria, razones = triage.sugerir_categoria({
        "motivo_consulta": motivo, "edad": datos.get("edad"),
    })
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO pacientes
           (turno_id, nombre, rut, edad, sexo, categoria_esi,
            motivo_consulta, estado, ingreso, creado_por,
            esi_sugerido, esi_razones)
           VALUES (?,?,?,?,?,?,?, 'en_atencion', ?, ?, ?, ?)""",
        (g.turno_activo["id"], datos["nombre"], datos.get("rut"),
         datos.get("edad"), datos.get("sexo"), categoria,
         motivo, ahora_iso(), g.usuario["id"], categoria,
         json.dumps(razones, ensure_ascii=False)),
    )
    pid = cur.lastrowid
    audit(conn, "fhir_ingreso", "paciente", pid, {"source": "SEIS"})
    conn.commit()
    conn.close()
    return jsonify({
        "resourceType": "Patient",
        "id": str(pid),
        "_meta": {"creado": True, "esi_sugerido": categoria}
    }), 201


# === Endpoints FHIR R4 para integración HIS ===

@app.route("/fhir/Patient/<int:pid>")
@login_required
def fhir_get_patient(pid):
    conn = get_conn()
    p = conn.execute("SELECT * FROM pacientes WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not p:
        return jsonify({"resourceType": "OperationOutcome",
                        "issue": [{"severity": "error", "code": "not-found"}]}), 404
    return jsonify(_fhir_mod.paciente_to_fhir(dict(p)))


@app.route("/fhir/Observation", methods=["POST"])
@limiter.limit("120 per minute")  # dispositivos pueden enviar varios SV/min
def fhir_post_observation():
    """
    Endpoint para que dispositivos médicos publiquen SV vía FHIR Observation.
    Auth por API key del dispositivo en header X-Device-Key.
    """
    api_key = request.headers.get("X-Device-Key", "")
    if not api_key:
        return jsonify({"error": "X-Device-Key requerida"}), 401
    conn = get_conn()
    dev = conn.execute(
        "SELECT * FROM dispositivos WHERE api_key=? AND activo=1", (api_key,)
    ).fetchone()
    if not dev:
        conn.close()
        return jsonify({"error": "dispositivo no autorizado"}), 401

    body = request.get_json(silent=True) or {}
    # extraer subject reference para identificar paciente
    subject = body.get("subject", {}).get("reference", "")
    if not subject.startswith("Patient/"):
        conn.close()
        return jsonify({"error": "subject.reference Patient/<id> requerido"}), 400
    try:
        pid = int(subject.split("/")[1])
    except (ValueError, IndexError):
        conn.close()
        return jsonify({"error": "pid inválido"}), 400

    try:
        sv = _fhir_mod.fhir_observation_to_sv(body)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

    # Sanitizar valores recibidos del dispositivo: el FHIR parser puede aceptar
    # cualquier número; acá enforcemos rangos fisiológicos.
    pa_v = _clamp_pa(sv.get("pa"))
    fc_v = _clamp_sv(sv.get("fc"), "fc", int)
    fr_v = _clamp_sv(sv.get("fr"), "fr", int)
    temp_v = _clamp_sv(sv.get("temp"), "temp", float)
    sato2_v = _clamp_sv(sv.get("sato2"), "sato2", int)
    glasgow_v = _clamp_sv(sv.get("glasgow"), "glasgow", int)
    hgt_v = _clamp_sv(sv.get("hgt"), "hgt", int)
    if all(v is None for v in (pa_v, fc_v, fr_v, temp_v, sato2_v, glasgow_v, hgt_v)):
        conn.close()
        return jsonify({"error": "ningún valor SV válido en rango fisiológico"}), 400

    conn.execute(
        """INSERT INTO signos_vitales
           (paciente_id, pa, fc, fr, temp, sato2, glasgow, hgt, autor_id, creado_en)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
        (pid, pa_v, fc_v, fr_v, temp_v, sato2_v, glasgow_v, hgt_v, ahora_iso()),
    )
    # actualizar SV actuales del paciente
    conn.execute(
        """UPDATE pacientes SET pa=COALESCE(?,pa), fc=COALESCE(?,fc), fr=COALESCE(?,fr),
                                temp=COALESCE(?,temp), sato2=COALESCE(?,sato2),
                                glasgow=COALESCE(?,glasgow), hgt=COALESCE(?,hgt)
           WHERE id=?""",
        (pa_v, fc_v, fr_v, temp_v, sato2_v, glasgow_v, hgt_v, pid),
    )
    # evaluar alertas
    try:
        nuevas = _alertas_mod.evaluar_paciente(conn, pid)
        if nuevas:
            _alertas_mod.registrar_alertas(conn, pid, nuevas, ahora_iso())
    except Exception as e:
        app.logger.warning(f"alertas (FHIR) fallaron: {e}")
    conn.execute(
        """INSERT INTO auditoria (actor_id, accion, recurso, recurso_id, detalle, ip, ts)
           VALUES (NULL, ?, ?, ?, ?, ?, ?)""",
        ("device_sv", "paciente", pid,
         json.dumps({"dispositivo_id": dev["id"], "serial": dev["serial"]}, ensure_ascii=False),
         request.remote_addr, ahora_iso()),
    )
    conn.commit()
    conn.close()
    return jsonify({"resourceType": "Observation", "_meta": {"created": True}}), 201


@app.route("/fhir/metadata")
def fhir_capability():
    """FHIR CapabilityStatement — describe el server."""
    return jsonify({
        "resourceType": "CapabilityStatement",
        "status": "active",
        "fhirVersion": "4.0.1",
        "format": ["json"],
        "rest": [{
            "mode": "server",
            "resource": [
                {"type": "Patient", "interaction": [{"code": "read"}]},
                {"type": "Observation", "interaction": [{"code": "create"}]},
            ],
        }],
    })


# ---------- Errores ----------

@app.errorhandler(403)
def e403(e):
    return render_template("error.html", code=403, msg="Acción no autorizada en este contexto"), 403


@app.errorhandler(404)
def e404(e):
    return render_template("error.html", code=404, msg="No encontrado"), 404


@app.errorhandler(413)
def e413(e):
    return render_template("error.html", code=413, msg="Archivo demasiado grande (máx 30 MB)"), 413


@app.errorhandler(500)
def e500(e):
    return render_template("error.html", code=500, msg="Error interno"), 500


# ---------- Main ----------

_csrf_exempt_blueprint()


if __name__ == "__main__":
    init_db()
    ip = get_ip_local()
    port = int(os.environ.get("PORT", "5050"))
    print("=" * 60)
    print("  Sistema de Entrega de Turno - Urgencias")
    print("=" * 60)
    print(f"  Local:    http://127.0.0.1:{port}")
    print(f"  Red LAN:  http://{ip}:{port}")
    print("=" * 60)
    print("  Comparte la URL de Red LAN con el equipo del turno.")
    print("  Ctrl+C para detener.")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)
