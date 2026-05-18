"""
Detección de patrones en signos vitales con generación de alertas.
Sin ML — reglas clínicas sobre la serie temporal de SV.

Reglas implementadas:
  - Deterioro hemodinámico: PA sistólica cae >20 en últimas 2 tomas
  - Taquicardia progresiva: FC sube >20 lpm en últimas 2 tomas
  - Hipoxemia progresiva: SatO2 cae >3% en últimas 2 tomas
  - Glasgow deteriorándose: GCS cae ≥2 puntos
  - Hipertermia mantenida: T° ≥38.5°C en ≥2 tomas consecutivas
  - Inestabilidad criterios: shock index (FC/PA_sist) > 1
"""
import sqlite3
from typing import Optional


def _parse_pa(pa: Optional[str]) -> Optional[int]:
    if not pa: return None
    try:
        return int(''.join(c for c in pa.split('/')[0] if c.isdigit()))
    except (ValueError, IndexError):
        return None


def evaluar_paciente(conn: sqlite3.Connection, paciente_id: int) -> list[dict]:
    """
    Lee las últimas N tomas de SV del paciente y genera alertas si detecta
    patrones de deterioro. Retorna lista de alertas nuevas (sin persistir).
    """
    rows = conn.execute(
        """SELECT * FROM signos_vitales WHERE paciente_id=?
           ORDER BY creado_en DESC LIMIT 5""",
        (paciente_id,),
    ).fetchall()
    if len(rows) < 2:
        return []  # necesitamos al menos 2 tomas para comparar

    nuevas = []
    ultima = rows[0]
    previa = rows[1]

    # Deterioro hemodinámico
    pa_u = _parse_pa(ultima["pa"])
    pa_p = _parse_pa(previa["pa"])
    if pa_u and pa_p:
        delta = pa_p - pa_u
        if delta >= 20:
            sev = "critico" if pa_u < 90 else "warn"
            nuevas.append({
                "tipo": "deterioro_pa",
                "severidad": sev,
                "mensaje": f"PA sistólica cayó {delta} mmHg ({pa_p}→{pa_u})",
                "datos": {"pa_previa": pa_p, "pa_ultima": pa_u},
            })

    # Taquicardia progresiva
    if ultima["fc"] and previa["fc"]:
        delta = ultima["fc"] - previa["fc"]
        if delta >= 20:
            sev = "critico" if ultima["fc"] >= 130 else "warn"
            nuevas.append({
                "tipo": "taquicardia_progresiva",
                "severidad": sev,
                "mensaje": f"FC subió {delta} lpm ({previa['fc']}→{ultima['fc']})",
                "datos": {"fc_previa": previa["fc"], "fc_ultima": ultima["fc"]},
            })

    # Hipoxemia
    if ultima["sato2"] and previa["sato2"]:
        delta = previa["sato2"] - ultima["sato2"]
        if delta >= 3:
            sev = "critico" if ultima["sato2"] < 90 else "warn"
            nuevas.append({
                "tipo": "hipoxemia_progresiva",
                "severidad": sev,
                "mensaje": f"SatO₂ cayó {delta}% ({previa['sato2']}→{ultima['sato2']})",
                "datos": {"sato2_previa": previa["sato2"], "sato2_ultima": ultima["sato2"]},
            })

    # Glasgow deteriorándose
    if ultima["glasgow"] and previa["glasgow"]:
        delta = previa["glasgow"] - ultima["glasgow"]
        if delta >= 2:
            sev = "critico" if ultima["glasgow"] <= 12 else "warn"
            nuevas.append({
                "tipo": "deterioro_neurologico",
                "severidad": sev,
                "mensaje": f"Glasgow cayó {delta} puntos ({previa['glasgow']}→{ultima['glasgow']})",
                "datos": {"gcs_previo": previa["glasgow"], "gcs_ultimo": ultima["glasgow"]},
            })

    # Hipertermia mantenida (≥2 tomas con T° ≥38.5)
    if ultima["temp"] and previa["temp"] and ultima["temp"] >= 38.5 and previa["temp"] >= 38.5:
        nuevas.append({
            "tipo": "hipertermia_mantenida",
            "severidad": "warn",
            "mensaje": f"T° elevada en 2 tomas consecutivas ({previa['temp']}, {ultima['temp']})",
            "datos": {"temp_previa": previa["temp"], "temp_ultima": ultima["temp"]},
        })

    # Shock index: FC/PA_sist > 1.0 sugiere riesgo de shock
    if ultima["fc"] and pa_u and pa_u > 0:
        si = ultima["fc"] / pa_u
        if si >= 1.0:
            nuevas.append({
                "tipo": "shock_index_elevado",
                "severidad": "critico" if si >= 1.3 else "warn",
                "mensaje": f"Shock index {si:.2f} (FC {ultima['fc']} / PA {pa_u})",
                "datos": {"shock_index": round(si, 2)},
            })

    return nuevas


def registrar_alertas(conn: sqlite3.Connection, paciente_id: int,
                      alertas: list[dict], ahora_iso: str) -> int:
    """Persiste alertas en la tabla. Dedupe básico: no crea alerta del mismo tipo
    si ya existe una no reconocida en las últimas 4h."""
    import json
    creadas = 0
    for a in alertas:
        existe = conn.execute(
            """SELECT 1 FROM alertas_sv
               WHERE paciente_id=? AND tipo=? AND reconocida=0
               AND creada_en > datetime('now','-4 hours') LIMIT 1""",
            (paciente_id, a["tipo"]),
        ).fetchone()
        if existe:
            continue
        conn.execute(
            """INSERT INTO alertas_sv
               (paciente_id, tipo, severidad, mensaje, datos, creada_en)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (paciente_id, a["tipo"], a["severidad"], a["mensaje"],
             json.dumps(a.get("datos", {}), ensure_ascii=False), ahora_iso),
        )
        creadas += 1
    return creadas
