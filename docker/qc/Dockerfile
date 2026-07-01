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

COPY docker/qc/qc_server.py .

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=20s \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["uvicorn", "qc_server:app", "--host", "0.0.0.0", "--port", "8001"]
