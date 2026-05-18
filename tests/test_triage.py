"""
Tests del algoritmo de triage ESI chileno.
Regresión: cualquier cambio en triage.py debe pasar estos casos.

Ejecutar:  .venv/bin/python -m pytest tests/ -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from triage import sugerir_categoria, descripcion_categoria


def assert_cat(esperada: str, datos: dict, mensaje: str = ""):
    cat, razones = sugerir_categoria(datos)
    assert cat == esperada, (
        f"{mensaje}: esperado {esperada}, obtenido {cat}. Razones: {razones}"
    )


# =============== C1 — Resucitación ===============

def test_c1_pcr_recuperado():
    assert_cat("C1", {
        "motivo_consulta": "PCR recuperado post RCP avanzada",
        "pa": "85/50", "fc": 110, "sato2": 88, "glasgow": 9,
    })


def test_c1_politrauma():
    assert_cat("C1", {
        "motivo_consulta": "Politraumatismo por accidente alta energía",
        "pa": "90/60", "fc": 125, "fr": 28,
    })


def test_c1_glasgow_critico():
    assert_cat("C1", {"motivo_consulta": "Encontrado inconsciente", "glasgow": 7})


def test_c1_sato2_critica():
    assert_cat("C1", {"motivo_consulta": "Disnea", "sato2": 85})


def test_c1_shock():
    assert_cat("C1", {"motivo_consulta": "Hipotensión severa", "pa": "75/40"})


def test_c1_bradicardia_extrema():
    assert_cat("C1", {"motivo_consulta": "Mareo", "fc": 35})


def test_c1_taquicardia_extrema():
    assert_cat("C1", {"motivo_consulta": "Palpitaciones", "fc": 165})


def test_c1_keyword_shock_septico():
    assert_cat("C1", {"motivo_consulta": "Shock séptico, foco urinario"})


def test_c1_hipotermia():
    assert_cat("C1", {"motivo_consulta": "Encontrado en exterior", "temp": 33.5})


# =============== C2 — Emergencia ===============

def test_c2_iam():
    assert_cat("C2", {
        "motivo_consulta": "Dolor torácico opresivo irradiado a brazo izquierdo",
        "antecedentes": "HTA, dislipidemia",
        "pa": "160/95", "fc": 102, "sato2": 94,
    })


def test_c2_acv():
    assert_cat("C2", {
        "motivo_consulta": "Hemiplejia derecha súbita y afasia",
        "antecedentes": "FA crónica con anticoagulación",
        "pa": "180/100", "glasgow": 14,
    })


def test_c2_sato2_borderline():
    assert_cat("C2", {"motivo_consulta": "Disnea progresiva", "sato2": 92})


def test_c2_glasgow_alterado():
    assert_cat("C2", {"motivo_consulta": "Confusión", "glasgow": 13})


def test_c2_cardiopata_dolor_atipico():
    """Modificador: cardiópata + dolor torácico atípico debería sugerir C2."""
    assert_cat("C2", {
        "motivo_consulta": "Dolor torácico atípico",
        "antecedentes": "Cardiopatía isquémica, IAM previo 2024",
        "pa": "135/85", "fc": 88, "sato2": 96,
    })


def test_c2_intento_suicida():
    assert_cat("C2", {"motivo_consulta": "Intento suicida con ingesta de medicamentos"})


def test_c2_hemorragia():
    assert_cat("C2", {"motivo_consulta": "Hematemesis profusa, melena"})


def test_c2_fiebre_extrema():
    assert_cat("C2", {"motivo_consulta": "Fiebre", "temp": 40.2})


def test_c2_taquicardia_moderada():
    assert_cat("C2", {"motivo_consulta": "Palpitaciones", "fc": 135})


# =============== C3 — Urgencia ===============

def test_c3_apendicitis():
    assert_cat("C3", {
        "motivo_consulta": "Dolor abdominal en fosa ilíaca derecha",
        "edad": 22, "temp": 38.4,
    })


def test_c3_cefalea_intensa():
    assert_cat("C3", {
        "motivo_consulta": "Cefalea intensa de inicio brusco con fotofobia",
        "edad": 38,
    })


def test_c3_fiebre_alta():
    assert_cat("C3", {"motivo_consulta": "Fiebre y mialgias", "temp": 38.7})


def test_c3_adulto_mayor_factor_riesgo():
    """Adulto mayor (≥75) sube automáticamente a C3."""
    assert_cat("C3", {
        "motivo_consulta": "Dolor lumbar leve", "edad": 82, "fc": 78, "sato2": 97,
    })


def test_c3_lactante():
    assert_cat("C3", {"motivo_consulta": "Fiebre baja", "edad": 1, "temp": 38.0})


def test_c3_crisis_hipertensiva():
    assert_cat("C3", {"motivo_consulta": "Crisis hipertensiva", "pa": "200/110"})


def test_c3_asma_descompensada():
    assert_cat("C3", {
        "motivo_consulta": "Crisis asmática con sibilancias",
        "edad": 30, "sato2": 96,
    })


# =============== C4 — Menor ===============

def test_c4_esguince():
    assert_cat("C4", {"motivo_consulta": "Esguince tobillo derecho", "edad": 28})


def test_c4_otitis():
    assert_cat("C4", {
        "motivo_consulta": "Otitis media aguda con otalgia",
        "edad": 25, "temp": 38.2,
    })


def test_c4_itu_baja():
    assert_cat("C4", {"motivo_consulta": "ITU baja, disuria", "edad": 30})


def test_c4_herida_menor():
    assert_cat("C4", {"motivo_consulta": "Herida cortante en mano que requiere sutura"})


# =============== C5 — No urgente ===============

def test_c5_control_postop():
    """Bug histórico: 'Control de heridas' debe ganar a 'herida'."""
    assert_cat("C5", {
        "motivo_consulta": "Control de heridas operatorias",
        "edad": 45, "fc": 72,
    })


def test_c5_control_simple():
    assert_cat("C5", {"motivo_consulta": "Control de medicamentos", "edad": 40})


# =============== Edge cases ===============

def test_sin_datos_devuelve_c4_default():
    cat, razones = sugerir_categoria({"motivo_consulta": "Malestar general"})
    assert cat in ("C4", "C5"), f"Esperado C4/C5 sin signos, obtenido {cat}"


def test_pa_invalida_no_crashea():
    cat, _ = sugerir_categoria({"motivo_consulta": "Test", "pa": "abc/xyz"})
    assert cat in ("C1","C2","C3","C4","C5")


def test_motivo_vacio_no_crashea():
    cat, _ = sugerir_categoria({"motivo_consulta": "", "fc": 80})
    assert cat in ("C1","C2","C3","C4","C5")


def test_solo_signos_vitales_normales():
    cat, _ = sugerir_categoria({
        "motivo_consulta": "Consulta general",
        "pa": "120/80", "fc": 75, "fr": 16, "temp": 36.5, "sato2": 98, "glasgow": 15,
    })
    assert cat == "C4"


def test_descripcion_categoria_todas():
    for c in ("C1","C2","C3","C4","C5"):
        d = descripcion_categoria(c)
        assert d and d != c, f"Descripción vacía para {c}"


def test_descripcion_categoria_invalida():
    assert descripcion_categoria("X9") == "X9"


# =============== Razones (explainability) ===============

def test_razones_no_vacias():
    _, razones = sugerir_categoria({"motivo_consulta": "Dolor torácico"})
    assert len(razones) >= 1


def test_razones_strings():
    _, razones = sugerir_categoria({"motivo_consulta": "Apendicitis"})
    for r in razones:
        assert isinstance(r, str) and len(r) > 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
