"""
Base de datos SQLite para sistema de entrega de turno - Urgencias.
Schema autocontenido, idempotente. Se ejecuta al iniciar la app.
"""
import sqlite3
import os
from datetime import datetime

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "urgencias.db")


def get_conn():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")     # concurrencia lectores/escritor
    conn.execute("PRAGMA synchronous = NORMAL")   # buen balance integridad/perf
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    rol TEXT NOT NULL CHECK(rol IN ('medico','eu','tens','admin')),
    rut TEXT UNIQUE,
    activo INTEGER NOT NULL DEFAULT 1,
    creado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turnos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL CHECK(tipo IN ('dia','noche')),
    fecha_inicio TEXT NOT NULL,
    fecha_cierre TEXT,
    medico_jefe_id INTEGER,
    eu_id INTEGER,
    tens_ids TEXT,
    notas_apertura TEXT,
    notas_cierre TEXT,
    estado TEXT NOT NULL DEFAULT 'activo' CHECK(estado IN ('activo','cerrado')),
    FOREIGN KEY (medico_jefe_id) REFERENCES usuarios(id),
    FOREIGN KEY (eu_id) REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS pacientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turno_id INTEGER NOT NULL,
    nombre TEXT NOT NULL,
    rut TEXT,
    edad INTEGER,
    sexo TEXT CHECK(sexo IN ('M','F','O')),
    categoria_esi TEXT NOT NULL CHECK(categoria_esi IN ('C1','C2','C3','C4','C5')),
    box TEXT,
    motivo_consulta TEXT,
    antecedentes TEXT,
    alergias TEXT,
    pa TEXT,
    fc INTEGER,
    fr INTEGER,
    temp REAL,
    sato2 INTEGER,
    glasgow INTEGER,
    hgt INTEGER,
    estado TEXT NOT NULL DEFAULT 'en_atencion'
        CHECK(estado IN ('en_atencion','alta','hospitalizado','traslado','fallecido','fugado')),
    ingreso TEXT NOT NULL,
    egreso TEXT,
    creado_por INTEGER,
    FOREIGN KEY (turno_id) REFERENCES turnos(id),
    FOREIGN KEY (creado_por) REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS signos_vitales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id INTEGER NOT NULL,
    pa TEXT, fc INTEGER, fr INTEGER, temp REAL, sato2 INTEGER, glasgow INTEGER, hgt INTEGER,
    eva INTEGER,
    autor_id INTEGER,
    creado_en TEXT NOT NULL,
    FOREIGN KEY (paciente_id) REFERENCES pacientes(id),
    FOREIGN KEY (autor_id) REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS notas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id INTEGER NOT NULL,
    contenido TEXT NOT NULL,
    autor_id INTEGER,
    creado_en TEXT NOT NULL,
    FOREIGN KEY (paciente_id) REFERENCES pacientes(id),
    FOREIGN KEY (autor_id) REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS pendientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id INTEGER NOT NULL,
    tipo TEXT NOT NULL CHECK(tipo IN ('examen','interconsulta','traslado','medicamento','otro')),
    descripcion TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente' CHECK(estado IN ('pendiente','en_curso','completado','cancelado')),
    creado_en TEXT NOT NULL,
    completado_en TEXT,
    autor_id INTEGER,
    FOREIGN KEY (paciente_id) REFERENCES pacientes(id),
    FOREIGN KEY (autor_id) REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS hospitales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    codigo TEXT UNIQUE,
    direccion TEXT,
    region TEXT,
    activo INTEGER NOT NULL DEFAULT 1,
    creado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispositivos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hospital_id INTEGER,
    serial TEXT UNIQUE,
    tipo TEXT NOT NULL CHECK(tipo IN ('monitor_sv','glucometro','oximetro','ecg','otro')),
    fabricante TEXT,
    modelo TEXT,
    box TEXT,
    api_key TEXT UNIQUE NOT NULL,
    activo INTEGER NOT NULL DEFAULT 1,
    creado_en TEXT NOT NULL,
    FOREIGN KEY (hospital_id) REFERENCES hospitales(id)
);

CREATE INDEX IF NOT EXISTS idx_dispositivos_api_key ON dispositivos(api_key);

CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER,
    accion TEXT NOT NULL,
    recurso TEXT NOT NULL,
    recurso_id INTEGER,
    detalle TEXT,
    ip TEXT,
    ts TEXT NOT NULL,
    FOREIGN KEY (actor_id) REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS alertas_sv (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id INTEGER NOT NULL,
    tipo TEXT NOT NULL,
    severidad TEXT NOT NULL CHECK(severidad IN ('info','warn','critico')),
    mensaje TEXT NOT NULL,
    datos TEXT,
    reconocida INTEGER NOT NULL DEFAULT 0,
    reconocida_por INTEGER,
    reconocida_en TEXT,
    creada_en TEXT NOT NULL,
    FOREIGN KEY (paciente_id) REFERENCES pacientes(id),
    FOREIGN KEY (reconocida_por) REFERENCES usuarios(id)
);

CREATE INDEX IF NOT EXISTS idx_auditoria_ts ON auditoria(ts);
CREATE INDEX IF NOT EXISTS idx_auditoria_recurso ON auditoria(recurso, recurso_id);
CREATE INDEX IF NOT EXISTS idx_alertas_paciente ON alertas_sv(paciente_id);
CREATE INDEX IF NOT EXISTS idx_alertas_no_reconocidas ON alertas_sv(reconocida);

CREATE INDEX IF NOT EXISTS idx_pacientes_turno ON pacientes(turno_id);
CREATE INDEX IF NOT EXISTS idx_pacientes_estado ON pacientes(estado);
CREATE INDEX IF NOT EXISTS idx_pacientes_estado_turno ON pacientes(estado, turno_id);
CREATE INDEX IF NOT EXISTS idx_turnos_estado ON turnos(estado);
CREATE INDEX IF NOT EXISTS idx_notas_paciente ON notas(paciente_id);
CREATE INDEX IF NOT EXISTS idx_notas_creado ON notas(creado_en);
CREATE INDEX IF NOT EXISTS idx_pendientes_paciente ON pendientes(paciente_id);
CREATE INDEX IF NOT EXISTS idx_pendientes_estado ON pendientes(estado);
CREATE INDEX IF NOT EXISTS idx_signos_paciente ON signos_vitales(paciente_id);
CREATE INDEX IF NOT EXISTS idx_signos_creado ON signos_vitales(creado_en);
"""


def _migrate(conn):
    """Migraciones idempotentes: añaden columnas si no existen."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pacientes)").fetchall()}
    if "esi_sugerido" not in cols:
        conn.execute("ALTER TABLE pacientes ADD COLUMN esi_sugerido TEXT")
    if "esi_razones" not in cols:
        conn.execute("ALTER TABLE pacientes ADD COLUMN esi_razones TEXT")
    if "hospital_id" not in cols:
        conn.execute("ALTER TABLE pacientes ADD COLUMN hospital_id INTEGER")

    # multi-tenant para turnos y usuarios
    for tabla in ("usuarios", "turnos"):
        c = {r["name"] for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}
        if "hospital_id" not in c:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN hospital_id INTEGER")

    # Seed hospital "default" si no existe ninguno (retro-compat)
    if not conn.execute("SELECT 1 FROM hospitales LIMIT 1").fetchone():
        from datetime import datetime as _dt
        conn.execute(
            "INSERT INTO hospitales (nombre, codigo, region, activo, creado_en) VALUES (?,?,?,1,?)",
            ("Hospital Demo", "DEFAULT", "Metropolitana", _dt.now().isoformat(timespec="seconds")),
        )
        # asignar registros existentes al hospital default
        hid = conn.execute("SELECT id FROM hospitales WHERE codigo='DEFAULT'").fetchone()[0]
        for tabla in ("pacientes", "usuarios", "turnos"):
            conn.execute(f"UPDATE {tabla} SET hospital_id=? WHERE hospital_id IS NULL", (hid,))


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate(conn)
    cur = conn.execute("SELECT COUNT(*) AS n FROM usuarios")
    if cur.fetchone()["n"] == 0:
        ahora = datetime.now().isoformat(timespec="seconds")
        seed = [
            ("Dr. María González", "medico", "11.111.111-1", 1, ahora),
            ("Dr. Juan Pérez", "medico", "12.222.222-2", 1, ahora),
            ("EU Carolina Soto", "eu", "13.333.333-3", 1, ahora),
            ("EU Pablo Rivas", "eu", "14.444.444-4", 1, ahora),
            ("TENS Andrea Morales", "tens", "15.555.555-5", 1, ahora),
            ("TENS Felipe Tapia", "tens", "16.666.666-6", 1, ahora),
            ("Administrador", "admin", "10.000.000-0", 1, ahora),
        ]
        conn.executemany(
            "INSERT INTO usuarios (nombre, rol, rut, activo, creado_en) VALUES (?,?,?,?,?)",
            seed,
        )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Base de datos inicializada: {DB_PATH}")
