"""
Microservice QC (Quality Control vocal) pour AudioBookStudio en Docker.

Expose en HTTP la même logique que tools/QC/create_voiceprint.py et
tools/QC/verify_voice.py, mais sous forme de service FastAPI afin que
l'orchestrateur (un autre conteneur) puisse l'appeler sans avoir à
exécuter un sous-processus Python isolé.

Le modèle ONNX est chargé une seule fois au démarrage (au lieu d'une fois
par appel comme dans la version CLI), ce qui accélère nettement les
évaluations par lot.

Les fichiers WAV et .npy sont échangés via un volume partagé monté au même
chemin que dans l'orchestrateur (ex: /app/Projects/...). Le service reçoit
donc des chemins absolus et lit/écrit directement sur le volume.
"""

import os

import numpy as np
import librosa
import onnxruntime as ort
from sklearn.metrics.pairwise import cosine_similarity
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Chemin du modèle ONNX (monté en volume avec tools/QC, ou copié dans l'image).
MODEL_PATH = os.environ.get("QC_MODEL_PATH", "/app/tools/QC/voxceleb.onnx")

app = FastAPI(title="AudioBookStudio QC Service")

# Chargement unique du modèle au démarrage du conteneur.
_session: ort.InferenceSession | None = None
_input_name: str | None = None


def _get_session():
    global _session, _input_name
    if _session is None:
        if not os.path.exists(MODEL_PATH):
            raise RuntimeError(f"Modèle ONNX introuvable : {MODEL_PATH}")
        _session = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
        _input_name = _session.get_inputs()[0].name
    return _session, _input_name


def get_embedding(wav_path: str):
    session, input_name = _get_session()
    audio, _ = librosa.load(wav_path, sr=16000)
    audio = audio.astype(np.float32).reshape(1, -1)
    outputs = session.run(None, {input_name: audio})
    return outputs[0]


class VoiceprintRequest(BaseModel):
    wav_path: str
    output_npy: str


class VerifyRequest(BaseModel):
    ref_npy: str
    wav_path: str


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": _session is not None}


@app.post("/voiceprint")
def create_voiceprint(req: VoiceprintRequest):
    """Crée une empreinte de référence (.npy) à partir d'un fichier WAV."""
    if not os.path.exists(req.wav_path):
        raise HTTPException(status_code=400, detail=f"Fichier audio introuvable : {req.wav_path}")
    try:
        embedding = get_embedding(req.wav_path)
        os.makedirs(os.path.dirname(req.output_npy), exist_ok=True)
        np.save(req.output_npy, embedding)
        return {
            "message": f"[OK] Empreinte de {req.wav_path} sauvée dans {req.output_npy}",
            "output_npy": req.output_npy,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la création de l'empreinte : {e}")


@app.post("/verify")
def verify(req: VerifyRequest):
    """Calcule le score de similarité cosinus entre une empreinte et un WAV."""
    if not os.path.exists(req.ref_npy):
        raise HTTPException(status_code=400, detail=f"Empreinte de référence introuvable : {req.ref_npy}")
    if not os.path.exists(req.wav_path):
        raise HTTPException(status_code=400, detail=f"Fichier audio introuvable : {req.wav_path}")
    try:
        ref_embedding = np.load(req.ref_npy)
        test_embedding = get_embedding(req.wav_path)
        score = float(
            cosine_similarity(
                ref_embedding.reshape(1, -1), test_embedding.reshape(1, -1)
            )[0][0]
        )
        return {"score": score}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors du calcul du score : {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
