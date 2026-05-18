# =====================================================================
# Sistema de Entrega de Turno · Urgencias
# Imagen multi-stage para producción.
# Tamaño esperado: ~1.2 GB (faster-whisper + ctranslate2 + weasyprint).
# =====================================================================

# ---------- Stage 1: builder ----------
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps mínimas para wheels que no tienen binarios pre-compilados
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install --prefix=/install -r requirements.txt


# ---------- Stage 2: runtime ----------
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5050 \
    THREADS=8 \
    PYTHONPATH=/app

# Runtime libs:
# - libgomp1: OpenMP runtime para ctranslate2 (STT)
# - libpango/libcairo/libffi/fontconfig: requeridos por WeasyPrint
# - fonts-dejavu-core + fonts-liberation: tipografía para PDF
# - libsndfile1: para wheel de soundfile (audio)
# - ca-certificates: HTTPS hacia HuggingFace al bajar el modelo
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libpango-1.0-0 libpangoft2-1.0-0 \
    libcairo2 libffi8 \
    libgdk-pixbuf-2.0-0 \
    libsndfile1 \
    fonts-dejavu-core fonts-liberation \
    fontconfig \
    ca-certificates \
    curl \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f

# Copiar Python packages instalados
COPY --from=builder /install /usr/local

# Crear usuario no-root
RUN useradd --create-home --shell /bin/bash --uid 1000 urgencias

WORKDIR /app

# Copiar código (orden por probabilidad de cambio: menos a más)
COPY --chown=urgencias:urgencias database.py triage.py stt.py llm.py \
     alertas.py busqueda.py fhir.py backup.py serve.py app.py ./
COPY --chown=urgencias:urgencias static/ ./static/
COPY --chown=urgencias:urgencias templates/ ./templates/
COPY --chown=urgencias:urgencias seed_demo.py ./

# Carpeta data persistente (mountear como volumen en EasyPanel)
RUN mkdir -p /app/data /app/data/whisper-cache /app/data/backups \
 && chown -R urgencias:urgencias /app/data

USER urgencias

# Healthcheck — EasyPanel lo lee
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT}/healthz || exit 1

EXPOSE 5050

# El entrypoint inicializa la BD + arranca waitress
CMD ["python", "serve.py"]
