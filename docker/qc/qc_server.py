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
import soundfile as sf
import soxr
import onnxruntime as ort
from sklearn.metrics.pairwise import cosine_similarity
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Ancien modèle (repli) : voxceleb.onnx via onnxruntime (waveform brute, 192-dim).
MODEL_PATH = os.environ.get("QC_MODEL_PATH", "/app/tools/QC/voxceleb.onnx")

# Modèle principal : ERes2Net (VoxCeleb) via sherpa-onnx — fbank intégré, CPU,
# meilleure discrimination du timbre (choisi après comparaison empirique).
# Si le fichier est absent, on retombe automatiquement sur voxceleb.onnx.
QC_SPEAKER_MODEL = os.environ.get("QC_SPEAKER_MODEL", "/app/models/speaker.onnx")
QC_NUM_THREADS = int(os.environ.get("QC_NUM_THREADS", "4"))
_USE_SHERPA = bool(QC_SPEAKER_MODEL) and os.path.exists(QC_SPEAKER_MODEL)
_sherpa_extractor = None

# Bibliothèque d'empreintes des voix VoiceBox (partagée avec l'orchestrateur).
VOICEPRINTS_DIR = os.environ.get("QC_VOICEPRINTS_DIR", "/app/Presets/voiceprints")

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


# Fréquence d'échantillonnage attendue par le modèle.
SR = 16000
# Tranches de durée (secondes) pré-calculées pour la référence, afin de
# comparer un chunk à une référence de durée équivalente (annule le biais
# de longueur du cosinus). On inclut des tranches courtes : même < 1 s, un
# gros écart de timbre (mauvaise voix) reste parfaitement visible.
SLICE_SECS = [0.5, 1.0, 2.0, 3.0, 5.0]
# Plancher : uniquement pour éviter le quasi-silence (< 0.3 s = clic/blanc).
# Au-dessus, on score toujours ; les scores < ~1.5 s sont marqués low_confidence.
FLOOR_SEC = 0.3
LOW_CONF_SEC = 1.5


def _load_wav_16k(path):
    """Charge un WAV en mono float32 à 16 kHz (soundfile + soxr, sans numba).

    On évite librosa ici : sa dépendance numba est instable dans cette image
    (crash JIT llvmlite) — soundfile/soxr sont des libs C, sûres.
    """
    data, sr = sf.read(path, dtype="float32")
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    if sr != SR:
        data = soxr.resample(data, sr, SR)
    return np.ascontiguousarray(data, dtype=np.float32)


def _get_sherpa():
    global _sherpa_extractor
    if _sherpa_extractor is None:
        import sherpa_onnx
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=QC_SPEAKER_MODEL,
            num_threads=QC_NUM_THREADS,
            provider="cpu",
        )
        _sherpa_extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    return _sherpa_extractor


def _embed_array(audio):
    """Calcule l'embedding locuteur d'un tableau audio mono 16 kHz.

    Utilise CAM++ via sherpa-onnx si dispo (fbank intégré), sinon repli sur
    voxceleb.onnx (onnxruntime, waveform brute).
    """
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    if _USE_SHERPA:
        ex = _get_sherpa()
        stream = ex.create_stream()
        stream.accept_waveform(SR, audio)
        stream.input_finished()
        emb = ex.compute(stream)
        return np.asarray(emb, dtype=np.float32)
    # Repli voxceleb
    session, input_name = _get_session()
    outputs = session.run(None, {input_name: audio.reshape(1, -1)})
    return outputs[0].reshape(-1).astype(np.float32)


def get_embedding(wav_path: str):
    audio = _load_wav_16k(wav_path)
    return _embed_array(audio).reshape(1, -1)


class VoiceprintRequest(BaseModel):
    wav_path: str
    output_npy: str


class VerifyRequest(BaseModel):
    ref_npy: str
    wav_path: str


class VoiceprintSaveRequest(BaseModel):
    wav_path: str
    voice_id: str


class ScoreRequest(BaseModel):
    voice_id: str
    wav_path: str


@app.get("/health")
def health():
    return {
        "status": "ok",
        "engine": "sherpa-eres2net" if _USE_SHERPA else "voxceleb",
        "speaker_model": QC_SPEAKER_MODEL if _USE_SHERPA else MODEL_PATH,
    }


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


@app.post("/voiceprint_save")
def voiceprint_save(req: VoiceprintSaveRequest):
    """Calcule les empreintes multi-durées d'un échantillon et les range.

    Pour chaque voix on stocke l'embedding du début de l'échantillon sur
    plusieurs tranches (1s/2s/3s/5s) + l'échantillon complet, dans un .npz.
    Cela permet, au scoring, de comparer un chunk à une référence de durée
    équivalente → le biais de longueur du cosinus s'annule.
    """
    if not os.path.exists(req.wav_path):
        raise HTTPException(status_code=400, detail=f"Fichier audio introuvable : {req.wav_path}")
    try:
        audio = _load_wav_16k(req.wav_path)
        total = len(audio) / SR
        arrays = {}
        for sec in SLICE_SECS:
            n = int(sec * SR)
            if n > 0 and len(audio) >= n:
                arrays[f"s{sec:g}"] = _embed_array(audio[:n])
        arrays["full"] = _embed_array(audio)
        arrays["full_dur"] = np.asarray([total], dtype=np.float32)

        os.makedirs(VOICEPRINTS_DIR, exist_ok=True)
        out_npz = os.path.join(VOICEPRINTS_DIR, f"{req.voice_id}.npz")
        np.savez(out_npz, **arrays)
        return {
            "npz": out_npz,
            "duration": total,
            "slices": [k for k in arrays if k not in ("full_dur",)],
            "dim": int(arrays["full"].shape[0]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'enregistrement de l'empreinte : {e}")


@app.post("/score")
def score(req: ScoreRequest):
    """Note un chunk contre la voix, en comparant à la référence de durée équivalente.

    - Charge les empreintes multi-durées de la voix (voice_id).
    - Calcule l'embedding + la durée du chunk.
    - Choisit la tranche de référence de durée la plus proche → cosinus.
    - Sous le plancher de durée → score None (n/a).
    """
    npz_path = os.path.join(VOICEPRINTS_DIR, f"{req.voice_id}.npz")
    if not os.path.exists(npz_path):
        raise HTTPException(status_code=404, detail=f"Empreinte introuvable pour la voix : {req.voice_id}")
    if not os.path.exists(req.wav_path):
        raise HTTPException(status_code=400, detail=f"Fichier audio introuvable : {req.wav_path}")
    try:
        audio = _load_wav_16k(req.wav_path)
        duration = len(audio) / SR
        if duration < FLOOR_SEC:
            return {"score": None, "duration": duration, "reason": "too_short"}

        chunk_emb = _embed_array(audio)
        data = np.load(npz_path)

        # Candidats (label, durée_référence, embedding)
        candidates = []
        for k in data.files:
            if k == "full_dur":
                continue
            if k == "full":
                candidates.append(("full", float(data["full_dur"][0]), data["full"]))
            elif k.startswith("s"):
                candidates.append((k, float(k[1:]), data[k]))
        if not candidates:
            raise Exception("Aucune tranche de référence disponible.")

        # Tranche de référence la plus proche en durée du chunk
        label, ref_sec, ref_emb = min(candidates, key=lambda c: abs(c[1] - duration))
        s = float(
            cosine_similarity(ref_emb.reshape(1, -1), chunk_emb.reshape(1, -1))[0][0]
        )
        return {
            "score": s,
            "duration": duration,
            "matched_ref_sec": ref_sec,
            "low_confidence": duration < LOW_CONF_SEC,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors du scoring : {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
