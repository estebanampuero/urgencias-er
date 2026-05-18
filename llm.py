"""
Integración LLM (Anthropic Claude) — opcional, gracefully degrade.

Funciones:
  - resumen_entrega_turno(): genera resumen narrativo de la entrega.
  - triage_complementario(): segunda opinión AI sobre la categoría ESI.

Configuración:
  export ANTHROPIC_API_KEY=sk-ant-...
  (opcional) export LLM_MODEL=claude-haiku-4-5-20251001

Uso de prompt caching para reducir costos en llamadas repetidas con
contexto clínico estable.
"""
import os
import json
from typing import Optional

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024

# Léxico clínico chileno que se cachea (>=1024 tokens para activar caching)
_LEXICO_CLINICO = """
Contexto clínico — Servicio de Urgencias hospitalario chileno.

Categorización ESI (Emergency Severity Index, MINSAL Chile):
- C1 RESUCITACIÓN: amenaza vital inmediata. PCR, shock, vía aérea comprometida,
  Glasgow ≤8, SatO₂ <90, PA sistólica <80, FC >150/<40, FR >35/<8, hipotermia.
- C2 EMERGENCIA: riesgo vital potencial. IAM, ACV agudo, sepsis, crisis convulsiva,
  intento suicida, hemorragia activa, Glasgow <15, SatO₂ <94, PA <90/>220, FC >130/<50,
  cetoacidosis diabética, abdomen agudo, parto en expulsivo.
- C3 URGENCIA: requiere atención prioritaria. Dolor abdominal, cefalea intensa,
  fiebre alta ≥38.5°C, cólico renal, asma exacerbada, crisis hipertensiva sin daño
  de órgano blanco, lumbago invalidante, deshidratación. Edad <2 o ≥75 sube a C3.
- C4 MENOR: patología aguda no urgente. Esguince, sutura, faringitis, otitis,
  ITU baja, cuerpo extraño, conjuntivitis.
- C5 NO URGENTE: control, curación, patología crónica leve.

Vocabulario clínico relevante:
HTA (hipertensión arterial), DM1/DM2 (diabetes mellitus tipo 1/2),
EPOC (enfermedad pulmonar obstructiva crónica), FA (fibrilación auricular),
IAM (infarto agudo de miocardio), ACV (accidente cerebrovascular),
ICC (insuficiencia cardíaca congestiva), ITU (infección tracto urinario),
TEC (traumatismo encefalocraneano), Glasgow (GCS, escala de coma 3-15),
SatO₂ (saturación de oxígeno %), PA (presión arterial mmHg), FC (frecuencia cardíaca lpm),
FR (frecuencia respiratoria rpm), HGT (hemoglucotest mg/dL), EVA (escala visual análoga 0-10),
NKDA (no known drug allergies).

Roles del equipo:
- Médico jefe: responsable clínico del turno.
- EU (Enfermera/o Universitaria/o): coordinación de cuidados.
- TENS (Técnico de Enfermería Nivel Superior): cuidados directos.

Estados del paciente: en_atencion, alta, hospitalizado, traslado, fallecido, fugado.

Estilo de respuesta:
- Español neutro chileno, técnico-clínico.
- Conciso, sin floritura.
- Priorizar pacientes C1/C2 y aquellos con SV deteriorándose.
- Mencionar nombres exactos de pacientes y números de box.
- No inventar datos clínicos que no estén en la entrada.
"""

_load_error: Optional[str] = None
try:
    import anthropic  # type: ignore
    _import_ok = True
except ImportError:
    _import_ok = False
    _load_error = "anthropic SDK no instalado"


def available() -> bool:
    return _import_ok and bool(os.environ.get("ANTHROPIC_API_KEY"))


def status() -> dict:
    return {
        "available": available(),
        "sdk_instalado": _import_ok,
        "api_key_seteada": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "modelo": os.environ.get("LLM_MODEL", _DEFAULT_MODEL),
        "error": _load_error,
    }


def _client():
    return anthropic.Anthropic()


def resumen_entrega_turno(datos: dict) -> dict:
    """
    Genera un resumen narrativo de la entrega de turno para el equipo entrante.

    Args:
        datos: dict con turno, equipo_saliente, equipo_entrante, pacientes (lista),
               stats. Mismo formato que la vista entrega.

    Returns:
        {"resumen": str, "tokens_in": int, "tokens_out": int, "cache_hit": bool}
        o {"error": str} si falla.
    """
    if not available():
        return {"error": "LLM no disponible (instalar anthropic + setear ANTHROPIC_API_KEY)"}

    # Construir prompt user con los datos del turno
    pacientes_resumen = []
    for p in datos.get("pacientes", []):
        if p.get("estado") != "en_atencion":
            continue
        linea = f"- [{p['categoria_esi']}] {p['nombre']}"
        if p.get("box"): linea += f" (Box {p['box']})"
        if p.get("edad"): linea += f", {p['edad']}a"
        linea += f". Motivo: {p.get('motivo_consulta','')}"
        if p.get("antecedentes"): linea += f". Antec: {p['antecedentes']}"
        sv = p.get("sv_ultima") or {}
        if sv: linea += f". SV: PA {sv.get('pa')} FC {sv.get('fc')} SatO2 {sv.get('sato2')}"
        if p.get("pendientes"):
            linea += f". Pendientes: " + "; ".join(pe["descripcion"] for pe in p["pendientes"])
        pacientes_resumen.append(linea)

    contenido = (
        f"Turno {datos.get('turno_tipo','').upper()}, "
        f"jefe saliente {datos.get('medico_saliente','')}, "
        f"EU {datos.get('eu_saliente','')}.\n\n"
        f"Pacientes activos ({len(pacientes_resumen)}):\n"
        + "\n".join(pacientes_resumen)
    )

    try:
        client = _client()
        msg = client.messages.create(
            model=os.environ.get("LLM_MODEL", _DEFAULT_MODEL),
            max_tokens=_MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": _LEXICO_CLINICO,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{
                "role": "user",
                "content": (
                    "Genera un resumen estructurado para la entrega de turno. "
                    "Formato:\n"
                    "1. Pacientes prioritarios (C1/C2) con acción pendiente.\n"
                    "2. Pacientes estables con pendientes (C3/C4).\n"
                    "3. Recomendaciones para el turno entrante.\n"
                    "Máximo 250 palabras. Estilo telegráfico-clínico.\n\n"
                    f"Datos:\n{contenido}"
                ),
            }],
        )
        texto = msg.content[0].text if msg.content else ""
        usage = msg.usage
        return {
            "resumen": texto,
            "tokens_in": getattr(usage, "input_tokens", 0),
            "tokens_out": getattr(usage, "output_tokens", 0),
            "cache_creation": getattr(usage, "cache_creation_input_tokens", 0),
            "cache_read": getattr(usage, "cache_read_input_tokens", 0),
        }
    except Exception as e:
        return {"error": f"LLM falló: {e}"}


def triage_complementario(datos_paciente: dict, categoria_reglas: str, razones_reglas: list) -> dict:
    """
    Segunda opinión AI sobre la categorización ESI ya calculada por el algoritmo.
    El LLM ve los mismos datos + la decisión de las reglas, y sugiere si está OK
    o si hay razones contextuales para hacer upgrade/downgrade.

    Returns:
        {"acuerdo": bool, "categoria_sugerida": str, "comentario": str, ...}
    """
    if not available():
        return {"error": "LLM no disponible"}

    payload = {
        "motivo": datos_paciente.get("motivo_consulta"),
        "antecedentes": datos_paciente.get("antecedentes"),
        "alergias": datos_paciente.get("alergias"),
        "edad": datos_paciente.get("edad"),
        "sexo": datos_paciente.get("sexo"),
        "sv": {k: datos_paciente.get(k) for k in ("pa","fc","fr","temp","sato2","glasgow","hgt")},
        "categoria_reglas": categoria_reglas,
        "razones_reglas": razones_reglas,
    }
    try:
        client = _client()
        msg = client.messages.create(
            model=os.environ.get("LLM_MODEL", _DEFAULT_MODEL),
            max_tokens=400,
            system=[{
                "type": "text",
                "text": _LEXICO_CLINICO,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": (
                    "Eres un segundo lector clínico. Las reglas categorizaron este paciente. "
                    "Responde SOLO con JSON válido (sin markdown, sin texto extra):\n"
                    '{"acuerdo": true|false, "categoria_sugerida": "C1"|"C2"|"C3"|"C4"|"C5", '
                    '"comentario": "razón breve, máx 2 oraciones"}\n\n'
                    f"Datos del paciente:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
                ),
            }],
        )
        texto = (msg.content[0].text if msg.content else "").strip()
        # extraer JSON (tolerar code fences)
        if texto.startswith("```"):
            texto = texto.strip("`")
            if texto.startswith("json"): texto = texto[4:].strip()
        try:
            data = json.loads(texto)
            data["tokens_in"] = msg.usage.input_tokens
            data["tokens_out"] = msg.usage.output_tokens
            return data
        except json.JSONDecodeError:
            return {"error": "LLM devolvió JSON inválido", "raw": texto[:200]}
    except Exception as e:
        return {"error": f"LLM falló: {e}"}
