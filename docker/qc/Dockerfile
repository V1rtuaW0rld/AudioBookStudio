# ============================================================
# QC — Service de contrôle qualité vocal (Python 3.10, CPU)
# Environnement isolé : onnxruntime + librosa + scikit-learn
# ============================================================
FROM python:3.10-slim

WORKDIR /app

# ffmpeg/libsndfile sont requis par librosa pour décoder les WAV.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY docker/qc/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Modèle d'empreinte vocale ERes2Net (VoxCeleb) pour sherpa-onnx — CPU.
# Choisi après comparaison empirique (CAM++/ResNet34/ERes2Net) : ERes2Net
# sépare de loin le mieux les voix (voix différentes ~0.09 de similarité).
RUN mkdir -p /app/models && \
    curl -fL -o /app/models/speaker.onnx \
    https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx

COPY docker/qc/qc_server.py .

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=20s \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["uvicorn", "qc_server:app", "--host", "0.0.0.0", "--port", "8001"]
