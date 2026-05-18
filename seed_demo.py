"""
seed_demo.py — Carga de datos de demostración.

Genera 1 semana completa (14 turnos día/noche) con ~28 pacientes y
sus notas, signos vitales y pendientes. El último turno queda activo.

Reejecutable: borra pacientes/turnos/notas/pendientes/signos_vitales
y mantiene/agrega usuarios. NO toca la BD real si se ha usado en
producción y NO hay turnos cerrados con datos reales — está pensado
para preparar un demo limpio.

Uso:  .venv/bin/python seed_demo.py
"""
import json
import random
from datetime import datetime, timedelta

from database import init_db, get_conn
import triage

random.seed(2026)  # reproducible

# ------------------------------------------------------------------
# Profesionales
# ------------------------------------------------------------------
PROFESIONALES = [
    ("Dr. María González",        "medico", "11.111.111-1"),
    ("Dr. Juan Pérez",            "medico", "12.222.222-2"),
    ("Dra. Carolina Henríquez",   "medico", "17.890.234-5"),
    ("Dr. Andrés Vargas",         "medico", "16.456.789-K"),
    ("Dra. Francisca Muñoz",      "medico", "18.123.456-7"),
    ("Dr. Roberto Silva",         "medico", "15.987.654-3"),
    ("Dra. Patricia Cárdenas",    "medico", "14.567.890-1"),

    ("EU Carolina Soto",          "eu", "13.333.333-3"),
    ("EU Pablo Rivas",            "eu", "14.444.444-4"),
    ("EU Daniela Espinoza",       "eu", "19.234.567-8"),
    ("EU Jorge Tapia",            "eu", "20.345.678-9"),
    ("EU Valentina Reyes",        "eu", "18.456.789-0"),
    ("EU Cristóbal Bravo",        "eu", "17.890.123-4"),

    ("TENS Andrea Morales",       "tens", "15.555.555-5"),
    ("TENS Felipe Tapia",         "tens", "16.666.666-6"),
    ("TENS Bárbara Núñez",        "tens", "19.876.543-2"),
    ("TENS Esteban Castro",       "tens", "20.123.456-7"),
    ("TENS Camila Quintana",      "tens", "18.234.567-K"),

    ("Administrador",             "admin", "10.000.000-0"),
]

# ------------------------------------------------------------------
# Pacientes — pool de nombres
# ------------------------------------------------------------------
NOMBRES = [
    ("Juan Carlos Pérez Mella", "M", 68),
    ("María Soledad Soto Vargas", "F", 54),
    ("Pedro Antonio González Rojas", "M", 72),
    ("Ana Isabel Martínez Bravo", "F", 38),
    ("Luis Felipe Cárdenas Núñez", "M", 45),
    ("Carmen Patricia Reyes Tapia", "F", 61),
    ("Roberto Andrés Vargas Espinoza", "M", 29),
    ("Daniela Constanza Henríquez", "F", 22),
    ("José Miguel Quintana Bravo", "M", 81),
    ("Fernanda Antonia Castro Silva", "F", 17),
    ("Cristián Alejandro Muñoz Reyes", "M", 35),
    ("Valentina Paz Espinoza Cárdenas", "F", 9),
    ("Sebastián Ignacio Tapia Núñez", "M", 52),
    ("Camila Andrea Rojas Vargas", "F", 28),
    ("Diego Andrés Bravo Soto", "M", 47),
    ("Sofía Catalina Pérez Henríquez", "F", 6),
    ("Manuel Eugenio Castro Mella", "M", 76),
    ("Javiera Antonia Silva Reyes", "F", 33),
    ("Hugo Patricio Núñez Vargas", "M", 58),
    ("Isabel Margarita Quintana Soto", "F", 84),
    ("Matías Esteban Rivas Pérez", "M", 41),
    ("Antonella Belén Morales Bravo", "F", 14),
    ("Rodrigo Esteban Cárdenas Silva", "M", 63),
    ("Constanza Belén Tapia Espinoza", "F", 26),
    ("Eduardo Antonio Vargas Reyes", "M", 70),
    ("Florencia Andrea Soto Núñez", "F", 19),
    ("Patricio Hernán Bravo Quintana", "M", 55),
    ("Rocío Belén Henríquez Rojas", "F", 31),
    ("Sergio Mauricio Espinoza Castro", "M", 49),
    ("Belén Isidora Mella Pérez", "F", 8),
]

# Casos por ESI: (motivo, antecedentes, alergias, (pa, fc, fr, temp, sato2, glasgow, hgt))
CASOS = {
    "C1": [
        ("PCR recuperado post RCP avanzada, 12 min de paro presenciado", "Cardiopatía isquémica, IAM 2024, stent x2", "NKDA", ("85/50", 110, 24, 36.2, 88, 9, 145)),
        ("Politraumatismo por accidente de alta energía, sospecha trauma torácico", "Sin antecedentes", "NKDA", ("90/60", 125, 28, 35.8, 91, 11, 105)),
        ("Status epiléptico convulsivo refractario a benzodiacepinas", "Epilepsia desde la infancia", "Carbamazepina", ("130/85", 115, 22, 38.2, 92, 7, 78)),
        ("Shock séptico, sospecha de foco urinario", "DM2 mal controlada, ITU recurrente", "Sulfas", ("80/45", 130, 26, 39.1, 89, 13, 280)),
    ],
    "C2": [
        ("Dolor torácico opresivo de 2h, irradiado a brazo izquierdo y mandíbula", "HTA, dislipidemia, tabaquismo activo 30 paquetes-año", "NKDA", ("160/95", 102, 22, 36.8, 94, 15, 110)),
        ("Hemiplejia derecha súbita con afasia, inicio hace 90 min, sospecha ACV isquémico", "FA crónica, anticoagulación con warfarina, HTA", "NKDA", ("180/100", 88, 18, 36.5, 96, 14, 95)),
        ("Crisis convulsiva tónico-clónica, primer episodio, post-ictal prolongado", "Sin antecedentes neurológicos", "NKDA", ("140/85", 95, 20, 37.2, 95, 14, 88)),
        ("Intento suicida con ingesta de 30 comprimidos de clonazepam", "Trastorno depresivo mayor, intento previo 2024", "Penicilina", ("110/70", 78, 14, 36.0, 93, 13, 102)),
        ("Disnea severa con sibilancias audibles, crisis asmática grave", "Asma persistente moderada, EPOC GOLD 2", "AAS", ("145/88", 115, 32, 37.0, 89, 15, 105)),
        ("Hemorragia digestiva alta con melena profusa", "Cirrosis hepática, várices esofágicas conocidas", "NKDA", ("95/60", 108, 20, 36.2, 95, 15, 90)),
        ("Cetoacidosis diabética, vómitos y polidipsia", "DM1 desde los 12 años, mal apego a tratamiento", "NKDA", ("105/65", 118, 28, 36.3, 96, 14, 425)),
        ("Trauma craneoencefálico moderado, Glasgow inicial 12", "Sin antecedentes", "NKDA", ("150/90", 88, 18, 37.0, 95, 12, 105)),
    ],
    "C3": [
        ("Dolor abdominal en fosa ilíaca derecha de 8h, vómitos, sospecha apendicitis", "Sin antecedentes quirúrgicos", "NKDA", ("125/78", 92, 18, 38.4, 97, 15, 105)),
        ("Cefalea intensa de inicio brusco con fotofobia y náuseas", "Migraña con aura", "NKDA", ("145/88", 80, 16, 37.0, 98, 15, 88)),
        ("Fiebre alta de 3 días, escalofríos, mialgias generalizadas", "Sano", "NKDA", ("118/72", 105, 20, 39.2, 97, 15, 92)),
        ("Lumbago agudo invalidante post esfuerzo, sin signos neurológicos", "Discopatía L4-L5 conocida", "NKDA", ("135/85", 88, 18, 36.7, 98, 15, 100)),
        ("Cólico renal izquierdo con hematuria macroscópica", "Litiasis renal previa 2023", "NKDA", ("140/85", 95, 18, 36.8, 98, 15, 95)),
        ("Vómitos profusos y deshidratación moderada, gastroenteritis aguda", "Sano", "NKDA", ("105/65", 102, 20, 37.5, 96, 15, 88)),
        ("Caída de propia altura con TEC leve, vómito post evento, sin pérdida de conciencia", "Sano", "NKDA", ("130/80", 85, 16, 36.5, 98, 15, 90)),
        ("Disnea progresiva 5 días, edema MMII, descompensación cardíaca", "ICC NYHA III, HTA, FA crónica", "NKDA", ("160/92", 95, 24, 36.4, 92, 15, 105)),
        ("Dolor torácico atípico, sin irradiación, descartar SCA", "HTA en tratamiento", "NKDA", ("142/86", 82, 18, 36.6, 97, 15, 98)),
        ("Crisis hipertensiva sin daño de órgano blanco", "HTA mal controlada, dislipidemia", "NKDA", ("210/115", 92, 20, 36.5, 96, 15, 105)),
    ],
    "C4": [
        ("Esguince tobillo derecho post caída deportiva", "Sano", "NKDA", ("125/78", 78, 16, 36.6, 99, 15, None)),
        ("Faringitis aguda con fiebre baja y odinofagia", "Sano", "NKDA", ("122/76", 88, 18, 38.0, 98, 15, None)),
        ("Cuerpo extraño en ojo derecho, ocupacional", "Sano", "NKDA", ("120/75", 76, 14, 36.5, 99, 15, None)),
        ("ITU baja no complicada, disuria y poliaquiuria", "ITU recurrente", "NKDA", ("125/78", 84, 16, 37.4, 99, 15, None)),
        ("Otitis media aguda con otalgia intensa", "Sano", "NKDA", ("118/72", 92, 18, 38.5, 98, 15, None)),
        ("Herida cortante palma de la mano, requiere sutura", "Sano", "NKDA", ("128/80", 85, 16, 36.7, 99, 15, None)),
        ("Reacción local intensa por picadura de avispa", "Sin alergias conocidas", "NKDA", ("122/75", 82, 16, 37.0, 99, 15, None)),
        ("Conjuntivitis bacteriana bilateral", "Sano", "NKDA", ("120/76", 74, 14, 36.6, 99, 15, None)),
    ],
    "C5": [
        ("Dolor crónico de rodilla, exacerbación leve", "Artrosis bilateral, sobrepeso", "NKDA", ("130/82", 76, 14, 36.4, 99, 15, None)),
        ("Control de heridas operatorias, cambio de apósito", "Colecistectomía hace 7 días", "NKDA", ("122/76", 72, 14, 36.5, 99, 15, None)),
        ("Cefalea tensional leve, consulta dirigida", "Migraña ocasional", "NKDA", ("125/78", 70, 14, 36.5, 99, 15, None)),
    ],
}

BOXES = ["Box 1", "Box 2", "Box 3", "Box 4", "Box 5", "Box 6",
         "Box 7", "Box 8", "Reanimación", "Pediatría", "Observación", "Pasillo 1", "Pasillo 2"]

NOTAS_TPL = [
    "Paciente vigil, hemodinámicamente estable. Evolución favorable.",
    "Se administra analgesia EV. Reevaluar en 30 minutos.",
    "Solicito ECG y enzimas cardíacas seriadas. Pendiente resultado.",
    "Pendiente evaluación por especialista de turno.",
    "Tolera bien la vía oral. Se inicia régimen liviano.",
    "Familia informada de evolución y plan terapéutico.",
    "Paciente sin dolor, signos vitales estables. Apto para alta médica.",
    "Curación de herida realizada, sin signos de infección.",
    "Saturación recuperada con oxigenoterapia a 2 L/min por naricera.",
    "Se contacta UCI para evaluación, en espera de cama.",
    "Persiste fiebre alta a pesar de antitérmicos. Hemocultivos enviados.",
    "Diuresis horaria adecuada. Balance hídrico positivo controlado.",
    "Se completa volumen con SF 1000 cc en bolo. Mejora hemodinámica.",
    "Se inicia esquema antibiótico empírico tras toma de cultivos.",
    "Paciente más conectado con el medio, GCS 14. Pupilas isocóricas.",
    "Se conversa con familia respecto a pronóstico reservado.",
]

PENDIENTES_TPL = [
    ("examen", "Hemograma + PCR"),
    ("examen", "ECG de control en 30 min"),
    ("examen", "Función renal y electrolitos"),
    ("examen", "Radiografía de tórax PA y lateral"),
    ("examen", "TAC cerebral sin contraste"),
    ("examen", "Ecografía abdominal urgente"),
    ("examen", "Troponinas seriadas en 6h"),
    ("examen", "Gases venosos"),
    ("examen", "Orina completa + urocultivo"),
    ("interconsulta", "Cardiología por dolor torácico"),
    ("interconsulta", "Neurología por crisis convulsiva"),
    ("interconsulta", "Cirugía por sospecha apendicitis"),
    ("interconsulta", "Traumatología por fractura"),
    ("interconsulta", "Psiquiatría por intento suicida"),
    ("interconsulta", "Medicina interna por descompensación"),
    ("traslado", "Coordinar traslado a hospital terciario"),
    ("traslado", "UCI - en espera de cama"),
    ("traslado", "Pabellón de urgencia"),
    ("medicamento", "Iniciar antibiótico EV ceftriaxona 2g"),
    ("medicamento", "Analgesia con morfina 3 mg cada 4h"),
    ("medicamento", "Furosemida 20 mg EV"),
    ("otro", "Conversar pronóstico con familia"),
    ("otro", "Coordinar alta con asistente social"),
]


def gen_rut():
    n = random.randint(2_000_000, 25_000_000)
    s = f"{n:,}".replace(",", ".")
    dv = random.choice("0123456789K")
    return f"{s}-{dv}"


def iso(dt):
    return dt.isoformat(timespec="seconds")


def pick_esi():
    r = random.random()
    if r < 0.04:  return "C1"
    if r < 0.18:  return "C2"
    if r < 0.55:  return "C3"
    if r < 0.88:  return "C4"
    return "C5"


def pick_estado_cerrado():
    r = random.random()
    if r < 0.55:  return "alta"
    if r < 0.75:  return "hospitalizado"
    if r < 0.83:  return "traslado"
    if r < 0.86:  return "fallecido"
    if r < 0.90:  return "fugado"
    return "en_atencion"


def pick_estado_activo():
    r = random.random()
    if r < 0.72:  return "en_atencion"
    if r < 0.90:  return "alta"
    if r < 0.97:  return "hospitalizado"
    return "traslado"


def clear_demo(conn):
    conn.executescript("""
        DELETE FROM pendientes;
        DELETE FROM notas;
        DELETE FROM signos_vitales;
        DELETE FROM pacientes;
        DELETE FROM turnos;
        DELETE FROM sqlite_sequence
          WHERE name IN ('turnos','pacientes','notas','pendientes','signos_vitales');
    """)


def ensure_users(conn):
    now = iso(datetime.now())
    for nombre, rol, rut in PROFESIONALES:
        row = conn.execute("SELECT id FROM usuarios WHERE nombre=?", (nombre,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO usuarios (nombre, rol, rut, activo, creado_en) VALUES (?,?,?,1,?)",
                (nombre, rol, rut, now),
            )


def main():
    init_db()
    conn = get_conn()
    clear_demo(conn)
    ensure_users(conn)
    conn.commit()

    medicos = [r["id"] for r in conn.execute("SELECT id FROM usuarios WHERE rol='medico' AND activo=1").fetchall()]
    eus     = [r["id"] for r in conn.execute("SELECT id FROM usuarios WHERE rol='eu' AND activo=1").fetchall()]
    tens    = [r["id"] for r in conn.execute("SELECT id FROM usuarios WHERE rol='tens' AND activo=1").fetchall()]
    assert medicos and eus and tens, "Faltan usuarios"

    # --- Calcular inicio del turno actual ---
    ahora = datetime.now().replace(microsecond=0)
    if ahora.hour < 8:
        inicio_actual = (ahora - timedelta(days=1)).replace(hour=20, minute=0, second=0)
    elif ahora.hour < 20:
        inicio_actual = ahora.replace(hour=8, minute=0, second=0)
    else:
        inicio_actual = ahora.replace(hour=20, minute=0, second=0)

    # --- 14 turnos: 13 cerrados + 1 activo ---
    turnos_meta = []
    for i in range(13, -1, -1):
        inicio = inicio_actual - timedelta(hours=12 * i)
        cierre = inicio + timedelta(hours=12)
        tipo = "dia" if inicio.hour == 8 else "noche"
        turnos_meta.append({"inicio": inicio, "cierre": cierre, "tipo": tipo, "activo": i == 0})

    nombre_idx = 0
    total_pacientes = 0

    for t in turnos_meta:
        mj = random.choice(medicos)
        eu = random.choice(eus)
        tens_set = random.sample(tens, k=random.randint(1, min(3, len(tens))))

        cur = conn.execute(
            """INSERT INTO turnos (tipo, fecha_inicio, fecha_cierre, medico_jefe_id, eu_id, tens_ids, notas_apertura, notas_cierre, estado)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                t["tipo"], iso(t["inicio"]),
                None if t["activo"] else iso(t["cierre"]),
                mj, eu, json.dumps(tens_set),
                "Turno iniciado sin novedades. Recursos disponibles según dotación habitual.",
                None if t["activo"] else (
                    "Sin eventos adversos. Pacientes con pendientes informados al turno entrante. "
                    "Se reporta disponibilidad de boxes y stock de medicamentos críticos."
                ),
                "activo" if t["activo"] else "cerrado",
            ),
        )
        turno_id = cur.lastrowid

        # Pacientes por turno: 1-3 (activo: 4-6 para tener volumen visible)
        n_pacs = random.randint(4, 6) if t["activo"] else random.randint(1, 3)

        for _ in range(n_pacs):
            if nombre_idx >= len(NOMBRES):
                nombre_idx = 0  # reciclar si se acaban (con jitter de RUT/edad)
                random.shuffle(NOMBRES)
            nombre, sexo, edad_base = NOMBRES[nombre_idx]
            nombre_idx += 1
            edad = max(1, min(95, edad_base + random.randint(-2, 2)))

            categoria = pick_esi()
            caso = random.choice(CASOS[categoria])
            motivo, antec, alergia, sv = caso
            pa, fc, fr, temp, sato2, gcs, hgt = sv

            # ingreso: entre 5 min y 11h tras el inicio del turno
            ingreso = t["inicio"] + timedelta(minutes=random.randint(5, 11 * 60))

            # estado
            if t["activo"]:
                estado = pick_estado_activo()
            else:
                estado = pick_estado_cerrado()

            # egreso si corresponde
            egreso = None
            if estado != "en_atencion":
                egreso_max = t["cierre"] if not t["activo"] else (ahora - timedelta(minutes=10))
                margen = max(30, int((egreso_max - ingreso).total_seconds() // 60))
                egreso = ingreso + timedelta(minutes=random.randint(30, margen)) if margen > 30 else egreso_max

            box = random.choice(BOXES) if estado == "en_atencion" else (random.choice(BOXES) if random.random() < 0.7 else None)

            # Sugerencia ESI del sistema para este paciente
            sug_cat, sug_razones = triage.sugerir_categoria({
                "motivo_consulta": motivo, "antecedentes": antec, "edad": edad,
                "pa": pa, "fc": fc, "fr": fr, "temp": temp,
                "sato2": sato2, "glasgow": gcs,
            })
            # En ~25% de los casos el médico difiere de la sugerencia
            # (refleja juicio clínico distinto al algoritmo)
            if random.random() < 0.25:
                vecinas = {"C1":"C2","C2":"C1","C3":"C2","C4":"C3","C5":"C4"}
                categoria = vecinas.get(sug_cat, sug_cat)
            else:
                categoria = sug_cat

            cur = conn.execute(
                """INSERT INTO pacientes
                   (turno_id, nombre, rut, edad, sexo, categoria_esi, box,
                    motivo_consulta, antecedentes, alergias,
                    pa, fc, fr, temp, sato2, glasgow, hgt,
                    estado, ingreso, egreso, creado_por,
                    esi_sugerido, esi_razones)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    turno_id, nombre, gen_rut(), edad, sexo,
                    categoria, box,
                    motivo, antec, alergia,
                    pa, fc, fr, temp, sato2, gcs, hgt,
                    estado, iso(ingreso),
                    iso(egreso) if egreso else None,
                    mj,
                    sug_cat, json.dumps(sug_razones, ensure_ascii=False),
                ),
            )
            pid = cur.lastrowid
            total_pacientes += 1

            # toma de SV inicial
            conn.execute(
                """INSERT INTO signos_vitales
                   (paciente_id, pa, fc, fr, temp, sato2, glasgow, hgt, autor_id, creado_en)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (pid, pa, fc, fr, temp, sato2, gcs, hgt, eu, iso(ingreso)),
            )

            # tomas adicionales (0-3) durante la estadía
            limite_sv = (egreso or t["cierre"] or ahora)
            for _ in range(random.randint(0, 3)):
                margen_sv = int((limite_sv - ingreso).total_seconds() // 60)
                if margen_sv < 20:
                    break
                t_sv = ingreso + timedelta(minutes=random.randint(15, margen_sv))
                # drift leve
                conn.execute(
                    """INSERT INTO signos_vitales
                       (paciente_id, pa, fc, fr, temp, sato2, glasgow, hgt, autor_id, creado_en)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pid, pa,
                        fc + random.randint(-8, 8) if fc else None,
                        fr + random.randint(-3, 3) if fr else None,
                        round(temp + random.uniform(-0.4, 0.4), 1) if temp else None,
                        max(80, min(100, sato2 + random.randint(-2, 3))) if sato2 else None,
                        gcs if gcs else None,
                        hgt + random.randint(-15, 15) if hgt else None,
                        random.choice([eu] + tens_set),
                        iso(t_sv),
                    ),
                )

            # notas (0-3)
            for _ in range(random.randint(0, 3)):
                margen_n = int((limite_sv - ingreso).total_seconds() // 60)
                if margen_n < 10:
                    break
                t_n = ingreso + timedelta(minutes=random.randint(10, margen_n))
                conn.execute(
                    "INSERT INTO notas (paciente_id, contenido, autor_id, creado_en) VALUES (?,?,?,?)",
                    (pid, random.choice(NOTAS_TPL), random.choice([mj, eu]), iso(t_n)),
                )

            # pendientes (0-3)
            for _ in range(random.randint(0, 3)):
                tipo_p, desc = random.choice(PENDIENTES_TPL)
                t_p = ingreso + timedelta(minutes=random.randint(5, 60))
                # estado del pendiente
                if t["activo"]:
                    estado_p = random.choices(
                        ["pendiente", "en_curso", "completado"],
                        weights=[0.45, 0.30, 0.25])[0]
                else:
                    estado_p = random.choices(
                        ["completado", "cancelado", "pendiente"],
                        weights=[0.70, 0.10, 0.20])[0]
                comp = None
                if estado_p == "completado":
                    cmax = int(((egreso or limite_sv) - t_p).total_seconds() // 60)
                    if cmax > 5:
                        comp = t_p + timedelta(minutes=random.randint(5, cmax))
                conn.execute(
                    """INSERT INTO pendientes
                       (paciente_id, tipo, descripcion, estado, creado_en, completado_en, autor_id)
                       VALUES (?,?,?,?,?,?,?)""",
                    (pid, tipo_p, desc, estado_p, iso(t_p),
                     iso(comp) if comp else None, random.choice([mj, eu])),
                )

    conn.commit()

    # Resumen
    n_t = conn.execute("SELECT COUNT(*) AS n FROM turnos").fetchone()["n"]
    n_t_act = conn.execute("SELECT COUNT(*) AS n FROM turnos WHERE estado='activo'").fetchone()["n"]
    n_p = conn.execute("SELECT COUNT(*) AS n FROM pacientes").fetchone()["n"]
    n_n = conn.execute("SELECT COUNT(*) AS n FROM notas").fetchone()["n"]
    n_pe = conn.execute("SELECT COUNT(*) AS n FROM pendientes").fetchone()["n"]
    n_sv = conn.execute("SELECT COUNT(*) AS n FROM signos_vitales").fetchone()["n"]
    n_u = conn.execute("SELECT COUNT(*) AS n FROM usuarios WHERE activo=1").fetchone()["n"]
    conn.close()

    print("=" * 56)
    print(" Seed de demostración cargado")
    print("=" * 56)
    print(f"  Usuarios activos:    {n_u}")
    print(f"  Turnos totales:      {n_t}  (activos: {n_t_act})")
    print(f"  Pacientes:           {n_p}")
    print(f"  Tomas SV:            {n_sv}")
    print(f"  Notas clínicas:      {n_n}")
    print(f"  Pendientes:          {n_pe}")
    print("=" * 56)


if __name__ == "__main__":
    main()
