"""
Algoritmo de sugerencia de categorización ESI (Emergency Severity Index)
adaptado a guías de triage chilenas (MINSAL / ESI v4 traducida).

ESI:
    C1 — Resucitación        : amenaza vital inmediata
    C2 — Emergencia          : riesgo vital, intervención rápida
    C3 — Urgencia            : requiere atención pero estable
    C4 — Menor               : patología aguda no urgente
    C5 — No urgente          : controles, patología crónica leve

Criterios principales (Chile/ESI):
    1. Signos vitales fuera de rangos críticos
    2. Glasgow alterado
    3. Palabras clave del motivo de consulta
    4. Modificadores etarios (<2, >75) y antecedentes (anticoagulación, cardiópata)

Esta función NO reemplaza el juicio clínico del personal de triage.
Es una sugerencia de apoyo que debe ser revisada y aceptada/modificada.
"""
from typing import Optional, Tuple, List, Dict, Any


def _parse_pa_sistolica(pa: Optional[str]) -> Optional[int]:
    if not pa:
        return None
    try:
        s = pa.split("/")[0].strip()
        return int("".join(ch for ch in s if ch.isdigit()))
    except (ValueError, IndexError):
        return None


def _has_kw(texto: str, palabras: List[str]) -> Optional[str]:
    """Retorna la primera palabra clave encontrada, o None."""
    t = (texto or "").lower()
    for kw in palabras:
        if kw in t:
            return kw
    return None


# Palabras clave por nivel (Chile)
KW_C1 = [
    "pcr", "paro card", "paro respira", "reanimaci", "sin pulso", "no respira",
    "inconsciente", "shock", "politraumat", "status epilept", "anafilax",
    "intubad", "intubaci", "via aerea", "vía aérea",
]

KW_C2 = [
    "iam", "infarto", "scaest", "scasest",
    "acv", "ictus", "isquemia cerebral", "hemiplej", "afasia",
    "sepsis", "shock séptico",
    "crisis convul", "convulsi", "status",
    "dolor torac", "dolor toráx", "dolor toracico", "dolor torácico",
    "disnea severa", "disnea grave", "insuficiencia respira",
    "hemorragia", "sangrado activ", "hematemesis", "melena",
    "intento suicid", "intoxicación grave",
    "trauma cráne", "tec grave", "tec moder",
    "parto", "expulsivo",
    "cetoacidosis", "hipoglicem severa",
    "abdomen agudo",
]

KW_C3 = [
    "dolor abdominal", "apendicit", "colecistit",
    "cefalea int", "cefalea sub", "migraña con aura",
    "fiebre alta", "fiebre >",
    "vomito", "vómito", "diarrea profusa", "deshidrat",
    "caída", "caida", "trauma menor", "tec leve",
    "cólico renal", "colico renal",
    "lumbago", "dolor severo",
    "asma", "crisis asmát", "broncoespasm",
    "crisis hipert", "ha descompensada",
    "descompensaci",
    "ihn", "itu compli", "pielonefr",
]

KW_C4 = [
    "esguince", "sutura", "herida", "contusión", "contusion",
    "faringitis", "amigdalit", "otitis",
    "cuerpo extraño", "cuerpo extrano",
    "itu baja", "cistitis",
    "conjuntiv", "blefarit",
    "picadura", "exantema", "urticaria leve",
    "dolor leve",
]

KW_C5 = [
    "control", "curación", "curacion", "cambio de aposito",
    "patología crónica", "patologia cronica",
    "consulta dirigi", "receta", "renovación",
]


def sugerir_categoria(datos: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    Recibe un dict con los datos del paciente y devuelve (categoria, razones).

    Campos esperados (todos opcionales salvo motivo):
        motivo_consulta : str
        antecedentes    : str
        edad            : int
        pa              : str ("120/80")
        fc, fr          : int
        temp            : float
        sato2, glasgow  : int
    """
    razones: List[str] = []
    motivo = (datos.get("motivo_consulta") or "").strip()
    antec = (datos.get("antecedentes") or "").strip()
    edad = datos.get("edad")
    fc = datos.get("fc")
    fr = datos.get("fr")
    temp = datos.get("temp")
    sato2 = datos.get("sato2")
    gcs = datos.get("glasgow")
    sis = _parse_pa_sistolica(datos.get("pa"))

    # =========================================================
    # C1 — Resucitación: amenaza vital inmediata
    # =========================================================
    if gcs is not None and gcs <= 8:
        razones.append(f"Glasgow {gcs} (≤8): compromiso neurológico severo")
        return "C1", razones
    if sato2 is not None and sato2 < 90:
        razones.append(f"SatO₂ {sato2}% (<90): insuficiencia respiratoria grave")
        return "C1", razones
    if sis is not None and sis < 80:
        razones.append(f"PA sistólica {sis} mmHg (<80): shock")
        return "C1", razones
    if fc is not None and (fc > 150 or fc < 40):
        razones.append(f"FC {fc} lpm (alterada críticamente): inestabilidad hemodinámica")
        return "C1", razones
    if fr is not None and (fr > 35 or fr < 8):
        razones.append(f"FR {fr} rpm (extrema): insuficiencia ventilatoria")
        return "C1", razones
    if temp is not None and temp < 34:
        razones.append(f"T° {temp}°C (<34): hipotermia severa")
        return "C1", razones
    kw = _has_kw(motivo, KW_C1) or _has_kw(antec, KW_C1)
    if kw:
        razones.append(f"Motivo/antecedente contiene «{kw}»")
        return "C1", razones

    # =========================================================
    # C2 — Emergencia
    # =========================================================
    if gcs is not None and gcs < 15:
        razones.append(f"Glasgow {gcs} (<15): alteración de conciencia")
        return "C2", razones
    if sato2 is not None and sato2 < 94:
        razones.append(f"SatO₂ {sato2}% (<94)")
        return "C2", razones
    if sis is not None and (sis < 90 or sis > 220):
        razones.append(f"PA sistólica {sis} mmHg (fuera de rango seguro)")
        return "C2", razones
    if fc is not None and (fc > 130 or fc < 50):
        razones.append(f"FC {fc} lpm (alterada)")
        return "C2", razones
    if fr is not None and (fr > 30 or fr < 10):
        razones.append(f"FR {fr} rpm (alterada)")
        return "C2", razones
    if temp is not None and (temp >= 39.5 or temp < 35):
        razones.append(f"T° {temp}°C (extrema)")
        return "C2", razones
    kw = _has_kw(motivo, KW_C2)
    if kw:
        razones.append(f"Motivo contiene «{kw}»")
        # Modificador: paciente cardiópata + dolor torácico → mantiene C2
        return "C2", razones

    # Modificador por antecedentes de alto riesgo + edad
    riesgo_cardio = _has_kw(antec, [
        "cardiopat", "isqu", "iam previo", "stent", "by-pass",
        "anticoagul", "warfarina", "marevan", "fa cron", "fa crónic",
    ])
    if riesgo_cardio and _has_kw(motivo, ["dolor toráx", "dolor torac", "dolor toraci", "disnea"]):
        razones.append(f"Antecedente «{riesgo_cardio}» + síntoma de alto riesgo")
        return "C2", razones

    # =========================================================
    # C3 — Urgencia
    # =========================================================
    if temp is not None and temp >= 38.5:
        razones.append(f"T° {temp}°C (≥38.5): fiebre alta")
        return "C3", razones
    if fc is not None and (fc > 110 or fc < 55):
        razones.append(f"FC {fc} lpm (alterada moderadamente)")
        return "C3", razones
    if sis is not None and (sis < 100 or sis > 180):
        razones.append(f"PA sistólica {sis} mmHg (alterada)")
        return "C3", razones
    if sato2 is not None and sato2 < 96:
        razones.append(f"SatO₂ {sato2}% (<96)")
        return "C3", razones
    kw = _has_kw(motivo, KW_C3)
    if kw:
        razones.append(f"Motivo contiene «{kw}»")
        return "C3", razones

    # Edad como factor de riesgo (sube a C3 si no encajó arriba)
    if edad is not None:
        if edad < 2:
            razones.append(f"Edad {edad} año(s): lactante (factor de riesgo)")
            return "C3", razones
        if edad >= 75:
            razones.append(f"Edad {edad} años: adulto mayor (factor de riesgo)")
            return "C3", razones

    # =========================================================
    # C5 — No urgente (se evalúa antes que C4 porque "control de herida"
    # debe ganar a "herida" sola)
    # =========================================================
    kw = _has_kw(motivo, KW_C5)
    if kw:
        razones.append(f"Motivo sugiere control o patología crónica («{kw}»)")
        return "C5", razones

    # =========================================================
    # C4 — Menor
    # =========================================================
    kw = _has_kw(motivo, KW_C4)
    if kw:
        razones.append(f"Motivo contiene «{kw}»")
        return "C4", razones

    # Por defecto: C4 (no se detectó nada relevante pero requiere atención)
    razones.append("Sin signos vitales alterados ni motivo de mayor severidad detectado")
    return "C4", razones


def descripcion_categoria(cat: str) -> str:
    return {
        "C1": "Resucitación — amenaza vital inmediata",
        "C2": "Emergencia — riesgo vital potencial",
        "C3": "Urgencia — atención prioritaria",
        "C4": "Menor — patología aguda no urgente",
        "C5": "No urgente — control o crónico",
    }.get(cat, cat)
