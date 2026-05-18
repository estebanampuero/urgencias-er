"""
STT local con faster-whisper (modelo `small`, int8 en CPU).
- Carga perezosa: el modelo solo se inicializa la primera vez que se transcribe.
- Cache del modelo en `data/whisper-cache/` (portable junto a la app).
- Vocabulario clínico inyectado por contexto vía `initial_prompt`.
- Dependencia opcional: si `faster-whisper` no está instalado,
  el módulo expone `available()=False` y los endpoints devuelven 503.
"""
import os
import threading
from typing import Optional

_MODEL_NAME = "small"
_DEVICE = "cpu"
_COMPUTE_TYPE = "int8"
_LANGUAGE = "es"

# Léxico clínico por contexto. faster-whisper usa `initial_prompt` como
# "sesgo" — no es prompt LLM, es señal acústica de qué palabras esperar.
_PROMPTS = {
    "general":     "Nota clínica de urgencias en español.",
    "motivo":      "Motivo de consulta: dolor torácico, disnea, cefalea, dolor abdominal, traumatismo, fiebre, vómitos, mareo.",
    "antecedentes":"Antecedentes: HTA, DM2, EPOC, cardiopatía isquémica, ACV, dislipidemia, hipotiroidismo, anticoagulación, asma.",
    "alergias":    "Alergias: penicilina, AINEs, sulfas, látex, NKDA, paracetamol.",
    "nota":        "Evolución clínica: paciente vigil, dolor, signos vitales, Glasgow, SatO2, presión arterial, indicaciones, fármacos.",
    "pendiente":   "Pendiente: examen, interconsulta, traslado, hemograma, ECG, TAC, radiografía, ecografía, panel metabólico.",
    "cierre":      "Resumen de turno: pacientes atendidos, altas, hospitalizaciones, traslados, eventos relevantes.",
}

_BASE_PROMPT = (
    "Transcripción médica de urgencias en español de Chile. "
    "Términos frecuentes: paciente, Glasgow, SatO2, HGT, AINEs, "
    "HTA, DM2, EPOC, dolor torácico, disnea, cefalea, ECG, TAC, hemograma, "
    "amlodipino, atorvastatina, metformina, omeprazol, paracetamol, ketoprofeno, "
    "tramadol, morfina, captopril, losartán, enalapril, salbutamol."
)

_model = None
_lock = threading.Lock()
_load_error: Optional[str] = None

try:
    from faster_whisper import WhisperModel  # type: ignore
    _import_ok = True
except Exception as e:
    _import_ok = False
    _load_error = f"faster-whisper no instalado ({e.__class__.__name__})"


def available() -> bool:
    """STT está disponible si el paquete se importó y no hubo error de carga previo."""
    return _import_ok and (_load_error is None or _model is not None)


def status() -> dict:
    return {
        "available": _import_ok,
        "modelo": _MODEL_NAME if _import_ok else None,
        "device": _DEVICE,
        "compute_type": _COMPUTE_TYPE,
        "cargado": _model is not None,
        "error": _load_error,
    }


def _ensure_model():
    global _model, _load_error
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        cache = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "whisper-cache")
        os.makedirs(cache, exist_ok=True)
        try:
            _model = WhisperModel(
                _MODEL_NAME, device=_DEVICE, compute_type=_COMPUTE_TYPE,
                download_root=cache,
            )
            _load_error = None
        except Exception as e:
            _load_error = f"Error cargando modelo: {e}"
            raise
    return _model


def transcribir(audio_path: str, contexto: str = "general") -> dict:
    if not _import_ok:
        return {"error": _load_error or "STT no disponible"}
    try:
        model = _ensure_model()
    except Exception as e:
        return {"error": f"No se pudo cargar el modelo: {e}"}

    prompt = _PROMPTS.get(contexto, _PROMPTS["general"]) + " " + _BASE_PROMPT
    try:
        segments, info = model.transcribe(
            audio_path,
            language=_LANGUAGE,
            initial_prompt=prompt,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            beam_size=5,
            condition_on_previous_text=False,
        )
        texto = " ".join(s.text.strip() for s in segments).strip()
        return {
            "texto": texto,
            "duracion": round(info.duration, 2),
            "idioma": info.language,
            "probabilidad_idioma": round(info.language_probability, 3),
        }
    except Exception as e:
        return {"error": f"Error en transcripción: {e}"}
