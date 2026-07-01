# ============================================================
# Orchestrateur AudioBookStudio (FastAPI + tools de pipeline)
# Python 3.11 — pilote VoiceBox (TTS) et le service QC en HTTP.
# ============================================================
FROM python:3.11-slim

WORKDIR /app

# ffmpeg requis par pydub (concaténation MP3) ; curl pour le healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY docker/orchestrator/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Code de l'orchestrateur et des outils de pipeline.
# Les scripts QC (create_voiceprint.py / verify_voice.py) ne sont PAS exécutés
# ici en Docker : ils tournent dans le conteneur QC. Mais on copie tout tools/
# pour les autres scripts (split, chunk, parse, epub2txt, concat, speak_text...).
COPY webapp/backend/ /app/webapp/backend/
COPY tools/ /app/tools/

# Projects/, Presets/, config/ sont fournis via volumes (voir docker-compose).

EXPOSE 17490

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=20s \
    CMD curl -f http://localhost:17490/api/projects || exit 1

# server.py écoute déjà sur 0.0.0.0:17490 quand lancé directement.
CMD ["python", "/app/webapp/backend/server.py"]
