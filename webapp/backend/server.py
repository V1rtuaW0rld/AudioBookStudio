import os
import re
import sys
import json
import shutil
import signal
import subprocess
import asyncio
import time
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Union, Any
from fastapi import FastAPI, HTTPException, Request, Query, UploadFile, File, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="AudioBookStudio Orchestrator API")

# Configure CORS so our frontend on port 5180 can access it
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths configuration
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
WEBAPP_DIR = os.path.dirname(BACKEND_DIR)
BASE_DIR = os.path.dirname(WEBAPP_DIR)
PROJECTS_DIR = os.path.join(BASE_DIR, "Projects")
TOOLS_DIR = os.path.join(BASE_DIR, "tools")
PYTHON_EXE = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")

if not os.path.exists(PYTHON_EXE):
    PYTHON_EXE = sys.executable  # Fallback to current runner python

# URLs configurables (Docker). Fallback = comportement local Windows actuel.
VOICEBOX_URL = os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493").rstrip("/")
# Si QC_URL est défini, le contrôle qualité vocal passe par le microservice
# HTTP (conteneur QC). Sinon, on retombe sur l'appel subprocess local (venv).
QC_URL = os.environ.get("QC_URL", "").rstrip("/")

# Ensure Projects directory exists
os.makedirs(PROJECTS_DIR, exist_ok=True)

# Presets directory configuration
PRESETS_DIR = os.path.join(BASE_DIR, "Presets")
NORMALIZATION_PRESETS_DIR = os.path.join(PRESETS_DIR, "Normalization")
os.makedirs(NORMALIZATION_PRESETS_DIR, exist_ok=True)

# Bibliothèque d'empreintes des voix VoiceBox (construite automatiquement,
# partagée avec le service qc via le montage de Presets). Une empreinte .npy
# par voix + un .meta.json (nom, durée de l'échantillon, sample_id).
VOICEPRINTS_DIR = os.path.join(PRESETS_DIR, "voiceprints")
os.makedirs(VOICEPRINTS_DIR, exist_ok=True)

# Cache for segment JSON metadata to speed up get_project_details
segment_cache = {}  # {project_name: {filename: {mtime: float, data: dict}}}

# --- Calibrage dynamique de la vitesse TTS (pour la barre de progression) ---
# On mesure la durée réelle de génération par caractère et on l'entretient en
# moyenne mobile exponentielle, par couple engine|model_size. Cela remplace la
# constante figée du frontend, qui dépendait du matériel/backend (CPU vs GPU,
# torch, taille du modèle…).
TTS_SPEED_FILE = os.path.join(PRESETS_DIR, "tts_speed.json")
TTS_SPEED_DEFAULT_BASE_MS = 500.0   # latence de base estimée (modèle chaud)
TTS_SPEED_DEFAULT_RATE_MS = 80.0    # ms/caractère par défaut tant qu'on n'a pas mesuré
TTS_SPEED_ALPHA = 0.3               # poids de la dernière mesure dans l'EMA

# --- Détection des générations TTS « emballées » (runaway) -------------------
# VoiceBox part parfois en vrille et produit ~11 min de borborygmes au lieu d'un
# court segment. On tue alors la tâche et on rejoue LE MÊME segment (sans purger
# la queue). Nuance clé : au COLD START (modèle Qwen pas encore chargé), un petit
# chunk peut légitimement tripler l'estimé → on laisse un plafond généreux ;
# une fois le modèle CHAUD, 3× l'estimé = dérapage certain → on tue.
RUNAWAY_FACTOR = 3.0            # seuil = 3 × temps estimé (modèle chaud)
RUNAWAY_WARM_FLOOR_S = 25.0     # plancher chaud (bruit d'estimation sur petits chunks)
RUNAWAY_COLD_MAX_S = 300.0      # plafond au démarrage (chargement modèle) : 5 min
RUNAWAY_WARM_WINDOW_S = 600.0   # « chaud » = dernier succès il y a moins de 10 min
RUNAWAY_MIN_SAMPLES = 3         # EMA jugée fiable au-delà de N mesures
RUNAWAY_MAX_RETRIES = 2         # relances du segment après emballement


def load_tts_speed() -> dict:
    try:
        with open(TTS_SPEED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_tts_engine_model() -> tuple:
    """Lit le moteur et la taille de modèle TTS depuis la config de génération."""
    try:
        with open(os.path.join(BASE_DIR, "config", "speak_config.json"), "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("engine", "qwen"), cfg.get("model_size", "1.7B")
    except Exception:
        return "qwen", "1.7B"


def update_tts_speed(engine: str, model_size: str, chars: int, duration_ms: float):
    """Met à jour l'EMA ms/caractère pour ce couple moteur/modèle."""
    if chars <= 0 or duration_ms <= 0:
        return
    data = load_tts_speed()
    key = f"{engine}|{model_size}"
    entry = data.get(key, {})
    base_ms = entry.get("base_ms", TTS_SPEED_DEFAULT_BASE_MS)
    observed = max(0.0, (duration_ms - base_ms)) / chars
    prev = entry.get("ms_per_char")
    new_rate = observed if prev is None else (TTS_SPEED_ALPHA * observed + (1 - TTS_SPEED_ALPHA) * prev)
    entry.update({
        "ms_per_char": round(new_rate, 3),
        "base_ms": base_ms,
        "samples": entry.get("samples", 0) + 1,
        "updated_at": time.time(),
    })
    data[key] = entry
    try:
        with open(TTS_SPEED_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Could not save tts_speed: {e}")


def estimate_gen_ms(chars: int) -> tuple:
    """Temps de génération estimé (ms) pour `chars` caractères, + nb de mesures
    de l'EMA (pour juger de sa fiabilité)."""
    eng, msz = get_tts_engine_model()
    entry = load_tts_speed().get(f"{eng}|{msz}", {})
    base = entry.get("base_ms", TTS_SPEED_DEFAULT_BASE_MS)
    rate = entry.get("ms_per_char", TTS_SPEED_DEFAULT_RATE_MS)
    est = base + max(0, chars) * rate
    return est, entry.get("samples", 0)


# Mount the Projects folder to serve WAV/MP3 files directly
app.mount("/audio", StaticFiles(directory=PROJECTS_DIR), name="audio")


class ProjectCreate(BaseModel):
    name: str
    type: str  # "novel", "theatre", "novel_multi"


class SegmentUpdate(BaseModel):
    text: str
    profile_id: Optional[str] = None


class SegmentSplitRequest(BaseModel):
    part1: Dict
    part2: Dict


class VoiceprintRequest(BaseModel):
    actor: str
    segment_num: int


class VoiceMappingUpdate(BaseModel):
    voice_mapping: Dict[str, str]


class CustomCharactersUpdate(BaseModel):
    custom_characters: List[str]


class NormalizationRule(BaseModel):
    id: str
    search: str
    replace: str
    active: Optional[bool] = True


class NormalizeRulesUpdate(BaseModel):
    normalization_rules: List[NormalizationRule]


class NormalizePreviewRequest(BaseModel):
    text: str
    remove_page_numbers: bool
    piece_style: Union[Dict[str, Any], str]
    normalization_rules: Optional[List[NormalizationRule]] = None


class NormalizeApplyRequest(BaseModel):
    remove_page_numbers: bool
    piece_style: Union[Dict[str, Any], str]
    text: Optional[str] = None
    normalization_rules: Optional[List[NormalizationRule]] = None
    # Roman : écrire le résultat DANS le txt source (pas de _formated), car le
    # pipeline roman ne parse pas — il découpe directement le txt source.
    overwrite_source: Optional[bool] = False


class NormalizationPreset(BaseModel):
    piece_style: Union[Dict[str, Any], str]
    remove_page_numbers: bool
    normalization_rules: List[NormalizationRule]


class PlayStyleItem(BaseModel):
    id: str
    name: str
    pattern: str
    system: Optional[bool] = False


class PlayStylesUpdate(BaseModel):
    custom_play_styles: List[PlayStyleItem]


class QueueRequest(BaseModel):
    ranges: str
    voice: Optional[str] = None
    versions: Optional[int] = 1
    qc_threshold: Optional[float] = None   # 0–100 ; None = mode normal (pas de boucle QC)
    max_attempts: Optional[int] = 20
    qc_batch: Optional[bool] = False       # True = résoudre le seuil par segment (meta.json)


def parse_ranges(expr: str) -> List[int]:
    if not expr:
        return []
    result = []
    parts = expr.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                start, end = part.split("-")
                result.extend(range(int(start), int(end) + 1))
            else:
                result.append(int(part))
        except ValueError:
            pass  # Ignore invalid entries
    return sorted(set(result))


def resolve_all_segments(project_dir: str) -> List[int]:
    tts_dir = os.path.join(project_dir, "tts")
    book_dir = os.path.join(project_dir, "book")
    if os.path.exists(tts_dir):
        files = [f for f in os.listdir(tts_dir) if f.startswith("seg") and f.endswith(".json")]
        if files:
            return sorted([int(f[3:8]) for f in files])
    if os.path.exists(book_dir):
        files = [f for f in os.listdir(book_dir) if f.startswith("seg") and f.endswith(".txt")]
        if files:
            return sorted([int(f[3:8]) for f in files])
    return []


class ProjectQueue:
    def __init__(self, project_name: str):
        self.project_name = project_name
        self.active_segment: Optional[int] = None
        self.logs: List[str] = []
        self.listeners: List[asyncio.Queue] = []

    def emit_log(self, message: str):
        formatted_line = f"data: {message}\n\n"
        self.logs.append(formatted_line)
        if len(self.logs) > 500:
            self.logs.pop(0)
        for listener in self.listeners:
            listener.put_nowait(formatted_line)
        print(message, flush=True)

    async def listen(self):
        q = asyncio.Queue()
        self.listeners.append(q)
        try:
            for log in self.logs:
                yield log
            while True:
                log = await q.get()
                yield log
        finally:
            self.listeners.remove(q)


global_project_queue = ProjectQueue("global")

def get_project_queue(name: str) -> ProjectQueue:
    return global_project_queue


def _normalize_voice_name(name: str) -> str:
    """Normalise un nom de voix pour la résolution (casse, espaces, apostrophes)."""
    if not name:
        return ""
    return name.strip().lower().replace("’", "'")


def resolve_voice_id(voice_name: str) -> Optional[str]:
    """Résout un nom de voix (generated_voice) vers un voice_id de la bibliothèque.

    Utilise l'index nom→id ; repli sur une comparaison normalisée (tolère les
    différences de casse/espaces/encodage).
    """
    if not voice_name:
        return None
    index_path = os.path.join(VOICEPRINTS_DIR, "index.json")
    if not os.path.exists(index_path):
        return None
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception:
        return None
    if voice_name in index:
        return index[voice_name]
    target = _normalize_voice_name(voice_name)
    for name, vid in index.items():
        if _normalize_voice_name(name) == target:
            return vid
    return None


# --- Seuils QC centralisés PAR VOIX (et non par projet/rôle) ----------------
# Le timbre calibré dépend de la voix VoiceBox, identique dans tous les
# ouvrages. On stocke donc les seuils dans un fichier central indexé par
# voice_id, réutilisé quel que soit le projet. L'UI reste par rôle : on
# traduit rôle→voix via le voice_mapping du projet.
CENTRAL_QC_THRESHOLDS_FILE = os.path.join(PRESETS_DIR, "qc_thresholds.json")

# Série de seuils par défaut attribuée à toute voix sans réglage (existante ou
# à venir). À affiner ensuite voix par voix ; évite la saisie à la chaîne.
# Buckets ≤0.5/≤1/≤2/≤3/≤4/≤5/≤6/≤7/≤10 s ; >10s (clé "15") laissé libre.
DEFAULT_QC_THRESHOLDS = {
    "0.5": 19.0, "1": 25.0, "2": 33.0, "3": 43.0, "4": 47.0,
    "5": 53.0, "6": 60.0, "7": 65.0, "10": 70.0,
}


def load_voice_thresholds() -> dict:
    try:
        with open(CENTRAL_QC_THRESHOLDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_voice_thresholds(data: dict):
    try:
        os.makedirs(PRESETS_DIR, exist_ok=True)
        with open(CENTRAL_QC_THRESHOLDS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] save_voice_thresholds: {e}")


def _project_voice_mapping(project_name: str) -> dict:
    meta_path = os.path.join(PROJECTS_DIR, project_name, "meta.json")
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f).get("voice_mapping", {}) or {}
    except Exception:
        return {}


def resolve_segment_voice_name(project_name: str, seg_data: dict) -> Optional[str]:
    """Nom de la VRAIE voix d'un segment pour le QC :
    1) `generated_voice` (voix réellement utilisée pour le WAV) si présent ;
    2) sinon voix mappée du rôle (`voice_mapping[profile_id]`) ;
    3) sinon voix du narrateur (NARRATEUR/Narrator).
    On ne renvoie JAMAIS un nom de rôle comme voix (ex. 'MARCEL')."""
    gv = seg_data.get("generated_voice")
    if gv:
        return gv
    pid = (seg_data.get("profile_id") or "").strip()
    mapping = _project_voice_mapping(project_name)
    narrator = mapping.get("NARRATEUR") or mapping.get("Narrator")
    mapped = mapping.get(pid)
    if pid and pid not in ("Narrator", "NARRATEUR", "DIDAS") and mapped and mapped != "DIDAS":
        return mapped
    return narrator


def build_role_thresholds(voice_mapping: dict) -> dict:
    """Construit la table par rôle à partir des seuils centraux par voix
    (pour l'affichage : chaque rôle → seuils de sa voix assignée)."""
    central = load_voice_thresholds()
    out = {}
    for role, voice_name in (voice_mapping or {}).items():
        vid = resolve_voice_id(voice_name)
        if vid and vid in central:
            out[role] = central[vid]
    return out


def build_voices_used(segments) -> dict:
    """Voix réellement utilisées (generated_voice) dans le projet + leurs seuils
    centraux, pour l'édition PAR VOIX dans QC (romans mono/multi-voix).
    Retour : { voice_name: {voice_id, thresholds} }."""
    central = load_voice_thresholds()
    out = {}
    for seg in segments:
        vn = seg.get("generated_voice")
        if not vn or vn in out:
            continue
        vid = resolve_voice_id(vn)
        out[vn] = {"voice_id": vid, "thresholds": (central.get(vid, {}) if vid else {})}
    return out


def migrate_qc_thresholds_to_central():
    """Migration unique : recopie les seuils par rôle des meta.json de projets
    vers le store central par voix (sans écraser une voix déjà présente)."""
    central = load_voice_thresholds()
    changed = False
    if not os.path.isdir(PROJECTS_DIR):
        return
    for proj in os.listdir(PROJECTS_DIR):
        meta_path = os.path.join(PROJECTS_DIR, proj, "meta.json")
        if not os.path.exists(meta_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        thr = meta.get("qc_thresholds") or {}
        mapping = meta.get("voice_mapping") or {}
        for role, buckets in thr.items():
            voice_name = mapping.get(role)
            if not voice_name:
                continue
            vid = resolve_voice_id(voice_name)
            if vid and vid not in central and buckets:
                central[vid] = buckets
                changed = True
                print(f"[MIGRATION] Seuils QC '{proj}'/{role} → voix {vid}")
    if changed:
        save_voice_thresholds(central)


def ensure_default_voice_thresholds():
    """Attribue la série par défaut à toute voix connue (index des empreintes)
    qui n'a aucun seuil dans le store central. Idempotent : ne touche pas aux
    voix déjà calibrées. Appelé au démarrage et après chaque sync d'empreintes."""
    index_path = os.path.join(VOICEPRINTS_DIR, "index.json")
    if not os.path.exists(index_path):
        return
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception:
        return
    central = load_voice_thresholds()
    changed = False
    for _name, vid in index.items():
        if not vid:
            continue
        entry = central.get(vid) or {}
        # Remplit chaque bucket MANQUANT avec sa valeur par défaut, sans écraser
        # les buckets déjà calibrés → les nouveaux calibres (0.5/4/6 s) arrivent
        # aussi sur les voix existantes.
        for bucket, val in DEFAULT_QC_THRESHOLDS.items():
            if bucket not in entry:
                entry[bucket] = val
                changed = True
        central[vid] = entry
    if changed:
        save_voice_thresholds(central)
        print(f"[QC] Calibres par défaut complétés pour les voix (buckets manquants).")


def _duration_bucket(d: float) -> str:
    """Mapping durée→bucket, identique au frontend (QC_BUCKETS)."""
    if d <= 0.5: return "0.5"
    if d <= 1:  return "1"
    if d <= 2:  return "2"
    if d <= 3:  return "3"
    if d <= 4:  return "4"
    if d <= 5:  return "5"
    if d <= 6:  return "6"
    if d <= 7:  return "7"
    if d <= 10: return "10"
    return "15"


def resolve_qc_threshold_for_segment(project_name: str, num: int) -> Optional[float]:
    """Résout le seuil QC d'un segment depuis le store central PAR VOIX
    (Presets/qc_thresholds.json[voice_id][bucket-durée]).

    La voix est celle réellement utilisée (generated_voice), sinon la voix
    assignée au rôle. Retourne None si aucun seuil configuré (→ génération
    simple) ou si la durée est inconnue.
    """
    project_dir = os.path.join(PROJECTS_DIR, project_name)
    json_path = os.path.join(project_dir, "tts", f"seg{str(num).zfill(5)}.json")
    if not os.path.exists(json_path):
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            seg_data = json.load(f)
    except Exception:
        return None
    profile_id = (seg_data.get("profile_id") or "").replace("’", "'")
    duration = seg_data.get("qc_duration")
    if duration is None:
        wav_path = os.path.join(project_dir, "audio", f"seg{str(num).zfill(5)}.wav")
        if os.path.exists(wav_path):
            duration = get_wav_duration(wav_path)
    if duration is None:
        return None
    # Voix réelle → voice_id. Repli sur la voix assignée au rôle.
    voice_name = seg_data.get("generated_voice") or _project_voice_mapping(project_name).get(profile_id)
    voice_id = resolve_voice_id(voice_name)
    if not voice_id:
        return None
    central = load_voice_thresholds()
    val = (central.get(voice_id, {}) or {}).get(_duration_bucket(float(duration)))
    if val in (None, ""):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


async def qc_score_native(voice_id: str, wav_path: str):
    """Note un chunk contre l'empreinte multi-durées d'une voix (service QC HTTP).

    Retourne (result_dict|None, error_text). result_dict = {score, duration, ...}
    où score peut être None (chunk trop court → n/a).
    """
    if not QC_URL:
        return None, "Service QC indisponible (QC_URL non défini)."
    loop = asyncio.get_event_loop()

    def _do():
        data = json.dumps({"voice_id": voice_id, "wav_path": wav_path}).encode("utf-8")
        req = urllib.request.Request(
            f"{QC_URL}/score",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        return await loop.run_in_executor(None, _do), ""
    except urllib.error.HTTPError as e:
        return None, e.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return None, str(e)


def set_segment_read(project_name: str, num: int, value: int):
    """Écrit le drapeau `read` (0 = audio non écouté, 1 = écouté/validé) dans le
    JSON du segment. Persistant côté serveur → partagé entre navigateurs
    (remplace le suivi par cache navigateur, non fiable)."""
    json_path = os.path.join(PROJECTS_DIR, project_name, "tts", f"seg{str(num).zfill(5)}.json")
    if not os.path.exists(json_path):
        return
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            seg_data = json.load(f)
        seg_data["read"] = int(value)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(seg_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] set_segment_read seg {num}: {e}")


def set_segment_qc_status(project_name: str, num: int, status: Optional[str]):
    """Écrit le statut QC manuel dans le JSON du segment.

    - 'forced'   : override manuel (clic droit) → ignoré par le tableau QC
                   même si la note n'est pas atteinte.
    - 'accepted' : validé (passé le seuil et approuvé) → ignoré aussi.
    - None/'clear': retire le statut (le segment repasse dans la logique normale).
    """
    json_path = os.path.join(PROJECTS_DIR, project_name, "tts", f"seg{str(num).zfill(5)}.json")
    if not os.path.exists(json_path):
        return
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            seg_data = json.load(f)
        if status in (None, "", "clear"):
            seg_data.pop("qc_status", None)
        else:
            seg_data["qc_status"] = status
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(seg_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] set_segment_qc_status seg {num}: {e}")


async def run_auto_evaluation(project_name: str, num: int, pq: Optional[ProjectQueue] = None):
    """Computes similarity score for a newly generated segment WAV and saves it to JSON."""
    project_dir = os.path.join(PROJECTS_DIR, project_name)
    tts_dir = os.path.join(project_dir, "tts")
    json_name = f"seg{str(num).zfill(5)}.json"
    json_path = os.path.join(tts_dir, json_name)

    if not os.path.exists(json_path):
        return

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            seg_data = json.load(f)
    except Exception:
        return

    # La voix réellement utilisée pour produire le WAV (jamais un nom de rôle).
    voice_name = resolve_segment_voice_name(project_name, seg_data)
    had_print = resolve_voice_id(voice_name) is not None
    # Empreinte manquante → on tente de la fabriquer une fois depuis VoiceBox.
    voice_id = ensure_voiceprint_for_name(voice_name)
    if not voice_id:
        # Vraiment aucune empreinte possible : rien à noter (jamais de faux rouge).
        if pq:
            pq.emit_log(f"[INFO] QC : pas d'empreinte pour la voix '{voice_name}' (échantillon VoiceBox manquant ?), segment {num} non noté.")
        return
    if not had_print and pq:
        pq.emit_log(f"[INFO] QC : empreinte créée automatiquement pour la voix '{voice_name}'.")

    wav_name = f"seg{str(num).zfill(5)}.wav"
    wav_path = os.path.join(project_dir, "audio", wav_name)
    if not os.path.exists(wav_path):
        return

    try:
        result, err = await qc_score_native(voice_id, wav_path)
        if result is None:
            if pq:
                pq.emit_log(f"[WARN] Echec de l'evaluation QC automatique : {err}")
            return
        # score peut être None (chunk trop court → n/a) : on l'écrit tel quel.
        seg_data["qc_score"] = result.get("score")
        seg_data["qc_duration"] = result.get("duration")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(seg_data, f, ensure_ascii=False, indent=2)
        if pq:
            sc = result.get("score")
            if sc is None:
                pq.emit_log(f"[INFO] QC segment {num} : n/a (chunk trop court, {result.get('duration', 0):.1f}s).")
            else:
                pq.emit_log(f"[INFO] QC segment {num} : similarite {sc*100:.0f}% (réf {result.get('matched_ref_sec')}s).")
    except Exception as e:
        if pq:
            pq.emit_log(f"[WARN] Echec de l'evaluation QC automatique : {str(e)}")


class GlobalTTSQueue:
    def __init__(self):
        self.pending_tasks: List[Dict] = []
        self.active_task: Optional[Dict] = None
        self.worker_task: Optional[asyncio.Task] = None
        self.active_process = None
        self.aborted = False
        self.last_gen_success: Optional[float] = None  # time.monotonic() du dernier succès (chaleur modèle)

    def _kill_active_group(self):
        """Tue DUREMENT le groupe de process de la génération en cours
        (generate_audio.py + speak_text.py) → ferme la socket VoiceBox. Distinct
        du stop « doux » qui laisse finir le TTS en cours."""
        p = self.active_process
        if not p:
            return
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    def add_segments(self, project_name: str, segment_nums: List[int], voice: Optional[str],
                     project_type: str, versions: int = 1,
                     qc_threshold: Optional[float] = None, max_attempts: int = 20,
                     qc_batch: bool = False):
        self.aborted = False
        pq = get_project_queue(project_name)
        if not self.active_task and not self.pending_tasks:
            pq.logs = []

        added = []
        for num in segment_nums:
            if (self.active_task and self.active_task["project_name"] == project_name and self.active_task["num"] == num) or \
               any(item["project_name"] == project_name and item["num"] == num for item in self.pending_tasks):
                continue
            self.pending_tasks.append({
                "project_name": project_name,
                "num": num,
                "voice": voice,
                "versions": versions,
                "project_type": project_type,
                "qc_threshold": qc_threshold,
                "max_attempts": max_attempts,
                "qc_batch": qc_batch,
            })
            added.append(num)

        if added:
            qc_info = f", seuil QC: {qc_threshold:.0f}%" if qc_threshold is not None else ""
            pq.emit_log(f"[INFO] File d'attente : ajout segments {added} projet '{project_name}' (voix: {voice or 'défaut'}, versions: {versions}{qc_info})")

        if not self.worker_task or self.worker_task.done():
            self.worker_task = asyncio.create_task(self.run_worker())

    def _runaway_timeout_s(self, seg_chars: int) -> tuple:
        """Délai au-delà duquel une génération est jugée « emballée ».
        - modèle CHAUD (dernier succès récent + EMA fiable) : 3 × estimé (plancher).
        - sinon (cold start / EMA jeune) : plafond généreux de chargement."""
        est_ms, samples = estimate_gen_ms(seg_chars)
        now = time.monotonic()
        warm = (self.last_gen_success is not None
                and (now - self.last_gen_success) < RUNAWAY_WARM_WINDOW_S
                and samples >= RUNAWAY_MIN_SAMPLES)
        if warm:
            return max(RUNAWAY_WARM_FLOOR_S, RUNAWAY_FACTOR * est_ms / 1000.0), True
        return RUNAWAY_COLD_MAX_S, False

    async def _exec_one_generation(self, cmd: list, pq, env: dict, seg_chars: int, out_wav: Optional[str] = None) -> str:
        """Lance une génération, stream les logs, met à jour l'EMA vitesse.
        Renvoie 'ok' | 'fail' | 'runaway'. Sur runaway (dépassement du seuil),
        tue DUREMENT le groupe de process (→ arrête VoiceBox) et renvoie 'runaway'."""
        gen_start = time.monotonic()
        timeout_s, warm = self._runaway_timeout_s(seg_chars)
        try:
            self.active_process = await asyncio.create_subprocess_exec(
                *cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=BASE_DIR, env=env, start_new_session=True,
            )

            async def _pump():
                while True:
                    line = await self.active_process.stdout.readline()
                    if not line:
                        break
                    pq.emit_log(line.decode('utf-8', errors='ignore').rstrip('\r\n'))

            pump_task = asyncio.create_task(_pump())
            try:
                await asyncio.wait_for(self.active_process.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                self._kill_active_group()
                pump_task.cancel()
                mode = "3× l'estimé, modèle chaud" if warm else "plafond de démarrage"
                pq.emit_log(f"[WARN] Génération emballée : dépassé {timeout_s:.0f}s ({mode}). "
                            f"Tâche VoiceBox tuée.")
                # WAV partiel/tronqué laissé par le kill → on le supprime pour ne
                # jamais conserver un audio incomplet.
                if out_wav and os.path.exists(out_wav):
                    try:
                        os.remove(out_wav)
                    except Exception:
                        pass
                return "runaway"

            await pump_task
            rc = self.active_process.returncode
            if rc == 0:
                if seg_chars > 0:
                    eng, msz = get_tts_engine_model()
                    update_tts_speed(eng, msz, seg_chars, (time.monotonic() - gen_start) * 1000.0)
                self.last_gen_success = time.monotonic()
                return "ok"
            return "fail"
        except Exception as e:
            if not self.aborted:
                pq.emit_log(f"[ERREUR] Impossible de lancer la génération : {str(e)}")
            return "fail"
        finally:
            self.active_process = None

    async def _run_generation_watchdog(self, cmd: list, pq, env: dict, seg_chars: int, out_wav: Optional[str] = None) -> bool:
        """Génère avec surveillance d'emballement : relance LE MÊME segment
        jusqu'à RUNAWAY_MAX_RETRIES fois si VoiceBox dérape. Renvoie True si succès.
        Ne purge PAS la queue : au retour, le worker enchaîne normalement."""
        attempts = RUNAWAY_MAX_RETRIES + 1
        for i in range(attempts):
            if self.aborted:
                return False
            status = await self._exec_one_generation(cmd, pq, env, seg_chars, out_wav)
            if status == "runaway" and not self.aborted and i < attempts - 1:
                pq.emit_log(f"[INFO] Relance du segment (tentative {i + 2}/{attempts}) après emballement.")
                continue
            if status == "runaway" and not self.aborted:
                pq.emit_log(f"[ERREUR] Emballement persistant après {attempts} tentatives — segment laissé sans audio fiable.")
            return status == "ok"
        return False

    async def run_worker(self):
        while self.pending_tasks:
            task = self.pending_tasks.pop(0)
            self.active_task = task

            project_name  = task["project_name"]
            num           = task["num"]
            voice         = task["voice"]
            versions      = task["versions"]
            project_type  = task["project_type"]
            qc_threshold  = task.get("qc_threshold")   # float 0-100, ou None
            max_attempts  = task.get("max_attempts", 20)

            # Batch QC : résoudre le seuil segment par segment depuis meta.json
            # (perso × bucket de durée). None → génération simple (comme le
            # bouton « régénérer » sur un segment sans seuil configuré).
            if task.get("qc_batch") and qc_threshold is None:
                qc_threshold = resolve_qc_threshold_for_segment(project_name, num)

            pq = get_project_queue(project_name)
            pq.active_segment = num

            project_dir = os.path.join(PROJECTS_DIR, project_name)
            audio_dir   = os.path.join(project_dir, "audio")
            os.makedirs(audio_dir, exist_ok=True)

            tts_dir   = os.path.join(project_dir, "tts")
            json_name = f"seg{str(num).zfill(5)}.json"
            json_path = os.path.join(tts_dir, json_name)

            # Efface le score QC du run précédent
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        seg_data = json.load(f)
                    if "qc_score" in seg_data:
                        del seg_data["qc_score"]
                        with open(json_path, "w", encoding="utf-8") as f:
                            json.dump(seg_data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"Warning: Failed to clear qc_score: {e}")

            # Longueur texte pour calibrage vitesse TTS
            seg_chars = 0
            try:
                if os.path.exists(json_path):
                    with open(json_path, "r", encoding="utf-8") as f:
                        seg_chars = len((json.load(f).get("text") or ""))
                else:
                    txt_p = os.path.join(project_dir, "book", f"seg{str(num).zfill(5)}.txt")
                    if os.path.exists(txt_p):
                        with open(txt_p, "r", encoding="utf-8") as f:
                            seg_chars = len(f.read().strip())
            except Exception:
                seg_chars = 0

            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"

            # Commande de génération (identique pour tous les runs)
            if project_type == "theatre":
                script_path = os.path.join(TOOLS_DIR, "generate_audio_theatre.py")
                cmd = [PYTHON_EXE, script_path, project_name, str(num)]
                if voice:
                    cmd.append(voice)
            else:
                # Roman : résolution rôle + voix PAR SEGMENT.
                # - chunk assigné à un personnage (custom_characters) → voix mappée
                #   (roman multi-voix : ex. EVA → Julia Roberts) ;
                # - sinon → narrateur (voix de la tâche). Rétro-compatible.
                seg_role = "Narrator"
                seg_voice = "Narrator"
                try:
                    meta_p = os.path.join(project_dir, "meta.json")
                    with open(meta_p, "r", encoding="utf-8") as f:
                        _meta = json.load(f)
                    _mapping = _meta.get("voice_mapping", {}) or {}
                    with open(json_path, "r", encoding="utf-8") as f:
                        _pid = (json.load(f).get("profile_id") or "").strip()
                    # Voix du narrateur (repli) : jamais un nom de rôle, toujours une
                    # vraie voix mappée -> indispensable pour que le QC retrouve
                    # l'empreinte et note le segment.
                    narrator_voice = (_mapping.get("NARRATEUR") or _mapping.get("Narrator")
                                      or voice or "Narrator")
                    # LE MAPPING FAIT FOI : si le rôle a une voix mappée (≠ DIDAS),
                    # on l'utilise — sans exiger sa présence dans custom_characters
                    # (les rôles détectés/non persistés étaient à tort renvoyés au
                    # narrateur, ex. « PAYSAN CRI »).
                    _mapped = _mapping.get(_pid)
                    if (_pid and _pid not in ("Narrator", "NARRATEUR", "DIDAS")
                            and _mapped and _mapped != "DIDAS"):
                        seg_role = _pid
                        seg_voice = _mapped
                    else:
                        seg_role = _pid or "Narrator"
                        seg_voice = narrator_voice
                except Exception:
                    pass
                script_path = os.path.join(TOOLS_DIR, "generate_audio.py")
                cmd = [PYTHON_EXE, script_path, project_name, str(num), seg_role, seg_voice]

            main_wav = os.path.join(audio_dir, f"seg{str(num).zfill(5)}.wav")

            if qc_threshold is not None and QC_URL:
                # ── MODE QC : boucle génération → score → vérification ──────
                voice_id_cache = [None]   # liste pour mutabilité dans closure sync

                def get_voice_id():
                    if voice_id_cache[0] is not None:
                        return voice_id_cache[0]
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            sd = json.load(f)
                        vn  = sd.get("generated_voice") or sd.get("profile_id")
                        # Empreinte créée à la volée si absente (nouvelle voix VoiceBox).
                        vid = ensure_voiceprint_for_name(vn)
                        if vid:
                            voice_id_cache[0] = vid
                        return vid
                    except Exception:
                        return None

                if versions == 1:
                    # Unitaire : rejouer jusqu'au seuil ou max_attempts.
                    # On conserve TOUJOURS la meilleure tentative en repli (jamais
                    # de segment sans audio, même si le seuil n'est pas atteint).
                    attempt   = 0
                    passed    = False
                    best_pct  = None
                    best_tmp  = os.path.join(audio_dir, f"seg{str(num).zfill(5)}.qcbest.wav")
                    while not self.aborted and attempt < max_attempts:
                        attempt += 1
                        pq.emit_log(f"[QC] Tentative {attempt}/{max_attempts} — segment {num}...")
                        pq.emit_log(f"[INFO] Commande : {' '.join(cmd)}")
                        ok = await self._run_generation_watchdog(cmd, pq, env, seg_chars, main_wav)
                        if not ok or not os.path.exists(main_wav):
                            break
                        vid = get_voice_id()
                        if not vid:
                            pq.emit_log(f"[QC] Pas d'empreinte connue pour cette voix — boucle QC désactivée.")
                            break
                        result, _err = await qc_score_native(vid, main_wav)
                        score = result.get("score") if result else None
                        if score is None:
                            pq.emit_log(f"[QC] Score n/a (chunk < 0.3 s) — segment {num} conservé.")
                            break
                        score_pct = round(score * 100)
                        pq.emit_log(f"[QC] Score : {score_pct}% (seuil : {qc_threshold:.0f}%)")
                        # Mémoriser la meilleure tentative
                        if best_pct is None or score_pct > best_pct:
                            best_pct = score_pct
                            try:
                                shutil.copyfile(main_wav, best_tmp)
                            except Exception:
                                pass
                        if score_pct >= qc_threshold:
                            passed = True
                            pq.emit_log(f"[QC] ✓ Segment {num} validé ({score_pct}% ≥ {qc_threshold:.0f}%).")
                            break
                        if attempt < max_attempts and not self.aborted:
                            pq.emit_log(f"[QC] Insuffisant (meilleur : {best_pct}%) — nouvelle tentative...")
                    # Repli : si aucune tentative n'a passé le seuil, restaurer la meilleure.
                    if not passed and best_pct is not None and os.path.exists(best_tmp):
                        try:
                            shutil.move(best_tmp, main_wav)
                            pq.emit_log(f"[QC] ⚠ Seuil non atteint en {attempt} tentative(s) — meilleure version conservée ({best_pct}%).")
                        except Exception:
                            pass
                    if os.path.exists(best_tmp):
                        try:
                            os.remove(best_tmp)
                        except Exception:
                            pass
                    # Écrire le score final dans le JSON
                    await run_auto_evaluation(project_name, num, pq)

                else:
                    # Multi-version : pour CHAQUE slot (version 1..N), on fait jusqu'à
                    # max_attempts essais et on garde le MEILLEUR de la série (ou on
                    # s'arrête dès qu'un essai passe le seuil). Au final on a N WAV =
                    # les N meilleurs, comparables même si aucun n'atteint le seuil.
                    produced = 0
                    for slot in range(1, versions + 1):
                        if self.aborted:
                            break
                        best_pct = None
                        best_tmp = os.path.join(audio_dir, f"seg{str(num).zfill(5)}.qcbest.wav")
                        passed   = False
                        attempt  = 0
                        while not self.aborted and attempt < max_attempts:
                            attempt += 1
                            pq.emit_log(f"[QC] Version {slot}/{versions} — tentative {attempt}/{max_attempts} (meilleur : {best_pct if best_pct is not None else '—'}%) — seg {num}...")
                            pq.emit_log(f"[INFO] Commande : {' '.join(cmd)}")
                            ok = await self._run_generation_watchdog(cmd, pq, env, seg_chars, main_wav)
                            if not ok or not os.path.exists(main_wav):
                                break
                            vid = get_voice_id()
                            result = None
                            if vid:
                                result, _err = await qc_score_native(vid, main_wav)
                            score = result.get("score") if result else None
                            if not vid or score is None:
                                # Pas d'empreinte / chunk trop court → accepter tel quel
                                dst = os.path.join(audio_dir, f"seg{str(num).zfill(5)}_v{slot}.wav")
                                os.replace(main_wav, dst)
                                best_pct = None
                                passed = True
                                reason = "pas d'empreinte" if not vid else "n/a — chunk court"
                                pq.emit_log(f"[QC] ✓ Version {slot}/{versions} acceptée ({reason}).")
                                break
                            score_pct = round(score * 100)
                            pq.emit_log(f"[QC] Score : {score_pct}% (seuil : {qc_threshold:.0f}%)")
                            if best_pct is None or score_pct > best_pct:
                                best_pct = score_pct
                                try:
                                    shutil.copyfile(main_wav, best_tmp)
                                except Exception:
                                    pass
                            if score_pct >= qc_threshold:
                                dst = os.path.join(audio_dir, f"seg{str(num).zfill(5)}_v{slot}.wav")
                                os.replace(main_wav, dst)
                                passed = True
                                pq.emit_log(f"[QC] ✓ Version {slot}/{versions} validée ({score_pct}% ≥ {qc_threshold:.0f}%) en {attempt} essai(s).")
                                break
                            if attempt < max_attempts and not self.aborted:
                                pq.emit_log(f"[QC] Insuffisant — nouvel essai pour la version {slot}...")
                        # Fin de la série : si pas passé, garder le meilleur de la série.
                        if not passed and best_pct is not None and os.path.exists(best_tmp):
                            dst = os.path.join(audio_dir, f"seg{str(num).zfill(5)}_v{slot}.wav")
                            try:
                                os.replace(best_tmp, dst)
                                pq.emit_log(f"[QC] ⚠ Version {slot}/{versions} : seuil non atteint — meilleur de la série conservé ({best_pct}%).")
                            except Exception:
                                pass
                        if os.path.exists(best_tmp):
                            try:
                                os.remove(best_tmp)
                            except Exception:
                                pass
                        if os.path.exists(os.path.join(audio_dir, f"seg{str(num).zfill(5)}_v{slot}.wav")):
                            produced += 1
                    pq.emit_log(f"[QC] Terminé : {produced}/{versions} version(s) produite(s) pour le segment {num}.")

            else:
                # ── MODE NORMAL ──────────────────────────────────────────────
                runs = range(1, versions + 1) if versions > 1 else [0]
                for run_idx in runs:
                    run_desc = f"version {run_idx}" if versions > 1 else "unique"
                    pq.emit_log(f"[INFO] Traitement du segment {num} ({run_desc}) pour '{project_name}' ...")
                    pq.emit_log(f"[INFO] Commande : {' '.join(cmd)}")
                    ok = await self._run_generation_watchdog(cmd, pq, env, seg_chars, main_wav)
                    if ok:
                        if versions > 1:
                            version_wav = os.path.join(audio_dir, f"seg{str(num).zfill(5)}_v{run_idx}.wav")
                            if os.path.exists(main_wav):
                                if os.path.exists(version_wav):
                                    os.remove(version_wav)
                                os.rename(main_wav, version_wav)
                                pq.emit_log(f"[INFO] Fichier renommé en seg{str(num).zfill(5)}_v{run_idx}.wav")
                            pq.emit_log(f"[INFO] Segment {num} ({run_desc}) généré avec succès.")
                        else:
                            await run_auto_evaluation(project_name, num, pq)
                            pq.emit_log(f"[INFO] Segment {num} ({run_desc}) généré avec succès.")
                    else:
                        if self.aborted:
                            pq.emit_log(f"[INFO] Génération annulée par l'utilisateur.")
                        else:
                            pq.emit_log(f"[ERREUR] Segment {num} ({run_desc}) a échoué.")

            # Nouvel audio produit → « non écouté » (read=0) + on efface tout statut
            # QC manuel obsolète (forced/accepted). Le segment est alors jugé
            # UNIQUEMENT par sa note vs son seuil : au-dessus = auto-fiable (masqué),
            # en-dessous = affiché (à traiter). Pas de validation manuelle du « bon ».
            if not self.aborted:
                set_segment_read(project_name, num, 0)
                set_segment_qc_status(project_name, num, None)

            pq.active_segment = None
            if project_name in segment_cache:
                segment_cache[project_name] = {}

        self.active_task = None

    async def stop(self):
        self.aborted = True
        self.pending_tasks.clear()
        if self.active_process:
            try:
                self.active_process.terminate()
            except Exception as e:
                print(f"Error terminating active process: {e}")


global_tts_queue = GlobalTTSQueue()



@app.get("/api/projects")
def list_projects():
    """Lists all audiobook projects in the Projects directory."""
    projects = []
    if not os.path.exists(PROJECTS_DIR):
        return []
        
    for name in os.listdir(PROJECTS_DIR):
        path = os.path.join(PROJECTS_DIR, name)
        if os.path.isdir(path):
            book_dir = os.path.join(path, "book")
            audio_dir = os.path.join(path, "audio")
            tts_dir = os.path.join(path, "tts")
            
            # Count segments (text or json)
            text_segs = 0
            json_segs = 0
            if os.path.exists(book_dir):
                text_segs = len([f for f in os.listdir(book_dir) if f.startswith("seg") and f.endswith(".txt")])
            if os.path.exists(tts_dir):
                json_segs = len([f for f in os.listdir(tts_dir) if f.startswith("seg") and f.endswith(".json")])
            
            # Total segment files
            total_segs = max(text_segs, json_segs)
            
            # Count generated WAV files
            wav_count = 0
            if os.path.exists(audio_dir):
                wav_count = len([f for f in os.listdir(audio_dir) if f.startswith("seg") and f.endswith(".wav")])
                
            # Check if any final MP3 exists
            mp3_exists = False
            if os.path.exists(path):
                mp3_exists = any(f.lower().endswith(".mp3") and os.path.isfile(os.path.join(path, f)) for f in os.listdir(path))
            
            # Determine project type from meta.json or dynamic fallback
            project_type = "novel"
            meta_path = os.path.join(path, "meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta_data = json.load(f)
                    project_type = meta_data.get("type", "novel")
                except Exception:
                    pass
            else:
                has_parsed_file = False
                if os.path.exists(book_dir):
                    has_parsed_file = any(f.lower().endswith("_parsed.txt") for f in os.listdir(book_dir))
                project_type = "theatre" if has_parsed_file else "novel"
            
            projects.append({
                "name": name,
                "type": project_type,
                "total_segments": total_segs,
                "generated_audio": wav_count,
                "has_final_mp3": mp3_exists,
            })
            
    return projects


@app.post("/api/projects")
def create_project(project: ProjectCreate):
    """Creates a new audiobook project directory structure."""
    name = project.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nom du projet invalide")
        
    path = os.path.join(PROJECTS_DIR, name)
    if os.path.exists(path):
        raise HTTPException(status_code=400, detail="Ce projet existe déjà")
        
    try:
        os.makedirs(os.path.join(path, "book"), exist_ok=True)
        os.makedirs(os.path.join(path, "audio"), exist_ok=True)
        os.makedirs(os.path.join(path, "tts"), exist_ok=True)
        
        # Write metadata
        meta_path = os.path.join(path, "meta.json")
        meta_data = {
            "name": name,
            "type": project.type
        }
        if project.type == "theatre":
            meta_data["custom_characters"] = ["DIDAS"]
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=2)
            
        return {"name": name, "message": "Projet créé avec succès"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/projects/{name}/upload")
async def upload_file(name: str, file: UploadFile = File(...)):
    """Uploads an EPUB or TXT file to the project's book directory."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(path) or not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
        
    book_dir = os.path.join(path, "book")
    os.makedirs(book_dir, exist_ok=True)
    
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    
    # Resolve project type from meta.json
    meta_path = os.path.join(path, "meta.json")
    project_type = "novel"
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
            project_type = meta_data.get("type", "novel")
        except Exception:
            pass

    if project_type == "theatre":
        if ext not in [".txt", ".pdf"]:
            raise HTTPException(status_code=400, detail="Seuls les fichiers .pdf et .txt sont acceptés")
    else:
        # Roman : EPUB ou PDF (PDF souvent plus simple à linéariser).
        if ext not in [".epub", ".pdf"]:
            raise HTTPException(status_code=400, detail="Seuls les fichiers .epub et .pdf sont acceptés")
        
    dest_path = os.path.join(book_dir, filename)
    try:
        content = await file.read()
        with open(dest_path, "wb") as f:
            f.write(content)
        return {"filename": filename, "message": f"Fichier {ext.upper()[1:]} téléversé avec succès"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def get_wav_duration(wav_path: str) -> float | None:
    try:
        import struct
        with open(wav_path, 'rb') as f:
            riff = f.read(12)
            if riff[0:4] != b'RIFF' or riff[8:12] != b'WAVE':
                return None
            while True:
                chunk_header = f.read(8)
                if len(chunk_header) < 8:
                    break
                chunk_id, chunk_size = struct.unpack('<4sI', chunk_header)
                if chunk_id == b'fmt ':
                    fmt_data = f.read(chunk_size)
                    sample_rate = struct.unpack('<I', fmt_data[4:8])[0]
                    bits_per_sample = struct.unpack('<H', fmt_data[14:16])[0]
                    num_channels = struct.unpack('<H', fmt_data[2:4])[0]
                elif chunk_id == b'data':
                    bytes_per_sample = bits_per_sample // 8
                    duration = chunk_size / (sample_rate * num_channels * bytes_per_sample)
                    return duration
                else:
                    f.seek(chunk_size, 1)
    except Exception:
        pass
    return None


@app.get("/api/projects/{name}")
def get_project_details(name: str):
    """Retrieves all segments and their audio status for a project."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(path) or not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
        
    book_dir = os.path.join(path, "book")
    audio_dir = os.path.join(path, "audio")
    tts_dir = os.path.join(path, "tts")
    
    # 1. Determine type and load voice mappings
    project_type = "novel"
    voice_mapping = {}
    custom_characters = []
    meta_path = os.path.join(path, "meta.json")
    meta_data = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
            project_type = meta_data.get("type", "novel")
            voice_mapping = {k.replace("’", "'"): v for k, v in meta_data.get("voice_mapping", {}).items()}
            custom_characters = [c.replace("’", "'") for c in meta_data.get("custom_characters", [])]
        except Exception:
            pass
    else:
        has_parsed_file = False
        if os.path.exists(book_dir):
            has_parsed_file = any(f.lower().endswith("_parsed.txt") for f in os.listdir(book_dir))
        project_type = "theatre" if has_parsed_file else "novel"
        
    # Scan tts directory using os.scandir to retrieve filenames and mtimes
    json_segs = []
    seg_mtimes = {}
    if os.path.exists(tts_dir):
        try:
            for entry in os.scandir(tts_dir):
                if entry.is_file() and entry.name.startswith("seg") and entry.name.endswith(".json"):
                    json_segs.append(entry.name)
                    seg_mtimes[entry.name] = entry.stat().st_mtime
        except Exception as e:
            print(f"[WARN] Error scanning tts directory: {e}")
    json_segs.sort()

    # Scan book directory using os.scandir for backward compatibility
    text_segs = []
    txt_mtimes = {}
    if os.path.exists(book_dir):
        try:
            for entry in os.scandir(book_dir):
                if entry.is_file() and entry.name.startswith("seg") and entry.name.endswith(".txt"):
                    text_segs.append(entry.name)
                    txt_mtimes[entry.name] = entry.stat().st_mtime
        except Exception as e:
            print(f"[WARN] Error scanning book directory: {e}")
    text_segs.sort()
    
    # Pre-scan audio directory for existing WAVs and candidate versions to avoid slow repeated I/O scans
    existing_wavs = set()
    versions_map = {}
    wav_mtimes = {}
    if os.path.exists(audio_dir):
        try:
            for entry in os.scandir(audio_dir):
                if entry.is_file() and entry.name.endswith(".wav"):
                    f = entry.name
                    if "_v" in f:
                        parts = f.split("_v")
                        if len(parts) == 2:
                            prefix = parts[0]
                            try:
                                seg_num = int(prefix[3:])
                                suffix = "v" + parts[1][:-4]  # remove .wav
                                if seg_num not in versions_map:
                                    versions_map[seg_num] = []
                                versions_map[seg_num].append(suffix)
                            except ValueError:
                                pass
                    elif f.startswith("seg"):
                        try:
                            seg_num = int(f[3:-4])
                            existing_wavs.add(seg_num)
                            wav_mtimes[seg_num] = entry.stat().st_mtime
                        except ValueError:
                            pass
        except Exception as e:
            print(f"[WARN] Error scanning audio directory: {e}")

    # Ensure project_cache exists
    if name not in segment_cache:
        segment_cache[name] = {}
    project_cache = segment_cache[name]

    # Clean up stale cache keys
    current_files = set(json_segs).union(set(text_segs))
    for k in list(project_cache.keys()):
        if k not in current_files:
            del project_cache[k]

    segments = []
    characters = set()
    if len(json_segs) > 0:
        # Load from tts/*.json (for both theatre and updated novels)
        for idx, filename in enumerate(json_segs, start=1):
            current_mtime = seg_mtimes.get(filename, 0)
            cached = project_cache.get(filename)
            if cached and cached["mtime"] == current_mtime:
                data = cached["data"]
            else:
                seg_path = os.path.join(tts_dir, filename)
                try:
                    with open(seg_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    project_cache[filename] = {
                        "mtime": current_mtime,
                        "data": data
                    }
                except Exception:
                    data = {}
            
            profile_id = data.get("profile_id", "Narrator").replace("’", "'")
            text = data.get("text", "")
            generated_voice = data.get("generated_voice")
            qc_score = data.get("qc_score")
            qc_duration = data.get("qc_duration")
            if project_type in ("theatre", "novel_multi"):
                characters.add(profile_id)
                
            wav_name = filename.replace(".json", ".wav")
            has_wav = idx in existing_wavs
            wav_mtime = wav_mtimes.get(idx) if has_wav else None

            if qc_duration is None and has_wav:
                wav_path = os.path.join(audio_dir, wav_name)
                qc_duration = get_wav_duration(wav_path)
                if qc_duration is not None:
                    data["qc_duration"] = qc_duration
                    try:
                        with open(seg_path, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
            
            available_versions = versions_map.get(idx, [])
            available_versions = sorted(available_versions, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)
 
            segments.append({
                "num": idx,
                "filename": filename,
                "profile_id": profile_id,
                "text": text,
                "has_audio": has_wav,
                "audio_url": f"/audio/{name}/audio/{wav_name}" if has_wav else None,
                "available_versions": available_versions,
                "generated_voice": generated_voice,
                "audio_mtime": wav_mtime,
                "qc_score": qc_score,
                "duration": qc_duration,
                # 0 = audio non écouté (nouveau, à tester), 1 = écouté/validé.
                # Absent (audio antérieur à la fonctionnalité) → considéré lu.
                "read": data.get("read", 1) if has_wav else 1,
                # Statut QC manuel : 'forced' (override clic droit) / 'accepted'
                # (validé) / None. Ignoré par le tableau QC si non-None.
                "qc_status": data.get("qc_status"),
            })
    elif len(text_segs) > 0:
        # Backward compatibility: Load from book/*.txt (for old novels)
        for idx, filename in enumerate(text_segs, start=1):
            current_mtime = txt_mtimes.get(filename, 0)
            cached = project_cache.get(filename)
            if cached and cached["mtime"] == current_mtime:
                text = cached["text"]
            else:
                seg_path = os.path.join(book_dir, filename)
                try:
                    with open(seg_path, "r", encoding="utf-8") as f:
                        text = f.read().strip()
                    project_cache[filename] = {
                        "mtime": current_mtime,
                        "text": text
                    }
                except Exception:
                    text = ""
            
            wav_name = filename.replace(".txt", ".wav")
            has_wav = idx in existing_wavs
            wav_mtime = wav_mtimes.get(idx) if has_wav else None
            
            available_versions = versions_map.get(idx, [])
            available_versions = sorted(available_versions, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)
 
            segments.append({
                "num": idx,
                "filename": filename,
                "profile_id": "Narrator",
                "text": text,
                "has_audio": has_wav,
                "audio_url": f"/audio/{name}/audio/{wav_name}" if has_wav else None,
                "available_versions": available_versions,
                "generated_voice": None,
                "audio_mtime": wav_mtime,
                "qc_score": None,
                "read": 1,
                "qc_status": None,
            })
            
    # Check EPUB/TXT/PDF files in book folder
    epub_files = [f for f in os.listdir(book_dir) if f.lower().endswith(".epub")] if os.path.exists(book_dir) else []
    pdf_files = [f for f in os.listdir(book_dir) if f.lower().endswith(".pdf")] if os.path.exists(book_dir) else []
    
    source_txt = None
    formated_txt = None
    parsed_txt = None
    chunked_txt = None
    
    if os.path.exists(book_dir):
        for f in os.listdir(book_dir):
            if f.lower().endswith(".txt") and not f.lower().startswith("seg"):
                if f.lower().endswith("_chunked.txt"):
                    chunked_txt = f
                elif f.lower().endswith("_parsed.txt"):
                    parsed_txt = f
                elif f.lower().endswith("_formated.txt"):
                    formated_txt = f
                else:
                    source_txt = f
    
    # Scan project directory for any compiled MP3 files
    mp3_files = []
    if os.path.exists(path):
        for f in os.listdir(path):
            if f.lower().endswith(".mp3") and os.path.isfile(os.path.join(path, f)):
                mp3_files.append({
                    "filename": f,
                    "url": f"/audio/{name}/{f}"
                })

    active_seg = None
    if global_tts_queue.active_task and global_tts_queue.active_task["project_name"] == name:
        active_seg = global_tts_queue.active_task["num"]

    global_queue_active = (global_tts_queue.active_task is not None) or (len(global_tts_queue.pending_tasks) > 0)

    pending_segments = [
        {"num": item["num"], "voice": item["voice"], "versions": item["versions"], "global_position": idx + 1}
        for idx, item in enumerate(global_tts_queue.pending_tasks)
    ]
    if project_type == "theatre":
        if "DIDAS" not in custom_characters:
            custom_characters.append("DIDAS")
        if os.path.exists(meta_path) and "custom_characters" not in meta_data:
            union_chars = set(custom_characters).union(characters)
            custom_characters = sorted(list(union_chars))
            try:
                meta_data["custom_characters"] = custom_characters
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta_data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[WARN] Error updating meta.json with default custom characters: {e}")
    elif project_type == "novel_multi":
        # NARRATEUR est le personnage originel (équivalent de DIDAS au théâtre).
        # On fusionne persos déclarés + détectés dans les segments, +NARRATEUR,
        # et on trie alphabétiquement (comme le théâtre).
        merged = set(custom_characters) | set(characters) | {"NARRATEUR"}
        merged.discard("Narrator")  # doublon technique du narrateur par défaut
        custom_characters = sorted(merged)

    response_data = {
        "name": name,
        "type": project_type,
        "epub_file": epub_files[0] if epub_files else None,
        "pdf_file": pdf_files[0] if pdf_files else None,
        "source_txt_file": source_txt,
        "formated_txt_file": formated_txt,
        "parsed_txt_file": parsed_txt,
        "chunked_txt_file": chunked_txt,
        "txt_file": chunked_txt or parsed_txt or formated_txt or source_txt,  # fallback
        "segments": segments,
        "characters": sorted(list(characters)),
        "custom_characters": custom_characters,
        # Personnages 'vus' dans les analyses IA enregistrées (≠ détectés dans les TTS).
        "ai_seen_characters": _ai_seen_characters(name),
        "voice_mapping": voice_mapping,
        "mp3_files": mp3_files,
        "active_segment": active_seg,
        "pending_segments": pending_segments,
        "global_queue_active": global_queue_active,
        "normalization_rules": meta_data.get("normalization_rules", []),
        # Seuils par rôle DÉRIVÉS du store central par voix (via voice_mapping).
        "qc_thresholds": build_role_thresholds(voice_mapping),
        # Voix réellement utilisées + leurs seuils centraux (édition par voix).
        "voices_used": build_voices_used(segments),
        "piece_style": meta_data.get("piece_style", "modern"),
        "custom_play_styles": (
            json.load(open(os.path.join(PRESETS_DIR, "custom_play_styles.json"), "r", encoding="utf-8"))
            if os.path.exists(os.path.join(PRESETS_DIR, "custom_play_styles.json"))
            else []
        )
    }

    return Response(
        content=json.dumps(response_data, ensure_ascii=False),
        media_type="application/json"
    )



class VersionSelectionRequest(BaseModel):
    version: str  # e.g. "v1", "v2", ...


@app.post("/api/projects/{name}/segments/{num}/select_version")
async def select_segment_version(name: str, num: int, data: VersionSelectionRequest):
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(project_dir) or not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")

    audio_dir = os.path.join(project_dir, "audio")
    if not os.path.exists(audio_dir):
        raise HTTPException(status_code=404, detail="Dossier audio introuvable")

    prefix = f"seg{str(num).zfill(5)}"
    selected_name = f"{prefix}_{data.version}.wav"
    selected_path = os.path.join(audio_dir, selected_name)
    main_path = os.path.join(audio_dir, f"{prefix}.wav")

    if not os.path.exists(selected_path):
        raise HTTPException(status_code=400, detail=f"Fichier de version {selected_name} introuvable")

    # Overwrite main WAV file with selected version
    try:
        if os.path.exists(main_path):
            os.remove(main_path)
        os.rename(selected_path, main_path)
        os.utime(main_path, None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Impossible de renommer la version sélectionnée: {e}")

    # Delete all other version files for this segment
    deleted_count = 0
    for f in os.listdir(audio_dir):
        if f.startswith(f"{prefix}_v") and f.endswith(".wav"):
            file_path = os.path.join(audio_dir, f)
            try:
                os.remove(file_path)
                deleted_count += 1
            except Exception as e:
                print(f"Warning: Failed to delete unselected version file {f}: {e}")

    # Auto-evaluate similarity
    await run_auto_evaluation(name, num)
    # Sélectionner une version = l'avoir écoutée/validée → read=1.
    set_segment_read(name, num, 1)

    if name in segment_cache:
        segment_cache[name] = {}

    return {
        "message": f"Version {data.version} validée pour le segment {num}. {deleted_count} versions alternatives supprimées.",
        "audio_url": f"/audio/{name}/audio/{prefix}.wav"
    }


@app.post("/api/projects/{name}/segments/{num}/mark_read")
async def mark_segment_read(name: str, num: int, value: int = Query(1)):
    """Marque un segment comme écouté (read=1) ou non (read=0), persistant en JSON."""
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(project_dir) or not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")
    json_path = os.path.join(project_dir, "tts", f"seg{str(num).zfill(5)}.json")
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Segment introuvable")
    set_segment_read(name, num, value)
    if name in segment_cache:
        segment_cache[name] = {}
    return {"num": num, "read": int(value)}


@app.post("/api/projects/{name}/segments/{num}/qc_status")
async def set_segment_qc_status_ep(name: str, num: int, status: str = Query("forced")):
    """Pose/retire le statut QC manuel d'un segment (forced / accepted / clear)."""
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(project_dir) or not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")
    json_path = os.path.join(project_dir, "tts", f"seg{str(num).zfill(5)}.json")
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Segment introuvable")
    if status not in ("forced", "accepted", "clear"):
        raise HTTPException(status_code=400, detail="Statut invalide (forced/accepted/clear).")
    set_segment_qc_status(name, num, status)
    if name in segment_cache:
        segment_cache[name] = {}
    return {"num": num, "qc_status": None if status == "clear" else status}






@app.put("/api/projects/{name}/segments/{num}")
def update_segment(name: str, num: int, data: SegmentUpdate):
    """Updates the text/voice of a single segment."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
        
    book_dir = os.path.join(path, "book")
    tts_dir = os.path.join(path, "tts")
    
    txt_name = f"seg{str(num).zfill(5)}.txt"
    json_name = f"seg{str(num).zfill(5)}.json"
    
    txt_path = os.path.join(book_dir, txt_name)
    json_path = os.path.join(tts_dir, json_name)
    
    if os.path.exists(json_path):
        # Theatre mode
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                seg_data = json.load(f)
            seg_data["text"] = data.text
            if data.profile_id:
                seg_data["profile_id"] = data.profile_id
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(seg_data, f, ensure_ascii=False, indent=2)
            # Invalider seulement ce segment (pas tout le projet) pour éviter
            # une relecture complète des JSON au prochain chargement.
            if name in segment_cache:
                segment_cache[name].pop(json_name, None)
            return {"message": f"Segment {num} mis à jour (JSON)"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    elif os.path.exists(txt_path):
        # Novel mode
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(data.text.strip() + "\n")
            # Invalider seulement ce segment (pas tout le projet).
            if name in segment_cache:
                segment_cache[name].pop(txt_name, None)
            return {"message": f"Segment {num} mis à jour (TXT)"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        raise HTTPException(status_code=404, detail="Segment introuvable")


@app.post("/api/projects/{name}/segments/{num}/split")
def split_segment(name: str, num: int, data: SegmentSplitRequest):
    """Splits a segment into two parts, shifting subsequent files by +1."""
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(project_dir) or not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")

    book_dir = os.path.join(project_dir, "book")
    tts_dir = os.path.join(project_dir, "tts")
    audio_dir = os.path.join(project_dir, "audio")

    # Find the maximum segment index currently in the project
    max_idx = 0
    if os.path.exists(tts_dir):
        files = [f for f in os.listdir(tts_dir) if f.startswith("seg") and f.endswith(".json")]
        for f in files:
            try:
                val = int(f[3:8])
                if val > max_idx:
                    max_idx = val
            except ValueError:
                pass
    if os.path.exists(book_dir):
        files = [f for f in os.listdir(book_dir) if f.startswith("seg") and f.endswith(".txt")]
        for f in files:
            try:
                val = int(f[3:8])
                if val > max_idx:
                    max_idx = val
            except ValueError:
                pass

    if num < 1 or num > max_idx:
        raise HTTPException(status_code=400, detail="Numéro de segment invalide")

    # Rename/shift all files from max_idx down to num + 1 in reverse order
    try:
        # Group version wav files by segment suffix to avoid O(N^2) os.listdir calls
        version_files_by_suffix = {}
        if os.path.exists(audio_dir):
            try:
                for f in os.listdir(audio_dir):
                    if f.startswith("seg") and "_v" in f and f.endswith(".wav"):
                        parts = f.split("_v")
                        if len(parts) == 2:
                            prefix = parts[0]
                            suffix = prefix[3:]
                            if suffix not in version_files_by_suffix:
                                version_files_by_suffix[suffix] = []
                            version_files_by_suffix[suffix].append(f)
            except Exception as e:
                print(f"[WARN] Error pre-scanning versions for split: {e}")

        for i in range(max_idx, num, -1):
            old_suffix = str(i).zfill(5)
            new_suffix = str(i + 1).zfill(5)

            # 1. Rename json in tts/
            if os.path.exists(tts_dir):
                old_json = os.path.join(tts_dir, f"seg{old_suffix}.json")
                new_json = os.path.join(tts_dir, f"seg{new_suffix}.json")
                if os.path.exists(old_json):
                    os.rename(old_json, new_json)

            # 2. Rename txt in book/
            if os.path.exists(book_dir):
                old_txt = os.path.join(book_dir, f"seg{old_suffix}.txt")
                new_txt = os.path.join(book_dir, f"seg{new_suffix}.txt")
                if os.path.exists(old_txt):
                    os.rename(old_txt, new_txt)

            # 3. Rename wav and version wavs in audio/
            if os.path.exists(audio_dir):
                old_wav = os.path.join(audio_dir, f"seg{old_suffix}.wav")
                new_wav = os.path.join(audio_dir, f"seg{new_suffix}.wav")
                if os.path.exists(old_wav):
                    os.rename(old_wav, new_wav)

                # Rename any version wav files (e.g. seg00647_v1.wav -> seg00648_v1.wav)
                prefix_old = f"seg{old_suffix}_v"
                prefix_new = f"seg{new_suffix}_v"
                for f in version_files_by_suffix.get(old_suffix, []):
                    os.rename(
                        os.path.join(audio_dir, f),
                        os.path.join(audio_dir, f.replace(prefix_old, prefix_new, 1))
                    )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors du décalage des fichiers : {e}")

    # Now write the new segment contents
    try:
        json_path_part1 = os.path.join(tts_dir, f"seg{str(num).zfill(5)}.json")
        json_path_part2 = os.path.join(tts_dir, f"seg{str(num + 1).zfill(5)}.json")

        if os.path.exists(tts_dir):
            with open(json_path_part1, "w", encoding="utf-8") as f:
                json.dump(data.part1, f, ensure_ascii=False, indent=2)
            with open(json_path_part2, "w", encoding="utf-8") as f:
                json.dump(data.part2, f, ensure_ascii=False, indent=2)

        # Handle txt files if this is book/novel mode
        txt_path_part1 = os.path.join(book_dir, f"seg{str(num).zfill(5)}.txt")
        txt_path_part2 = os.path.join(book_dir, f"seg{str(num + 1).zfill(5)}.txt")
        if os.path.exists(txt_path_part1) or os.path.exists(txt_path_part2) or not os.path.exists(tts_dir):
            os.makedirs(book_dir, exist_ok=True)
            with open(txt_path_part1, "w", encoding="utf-8") as f:
                f.write(data.part1.get("text", "").strip() + "\n")
            with open(txt_path_part2, "w", encoding="utf-8") as f:
                f.write(data.part2.get("text", "").strip() + "\n")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'écriture des nouveaux segments : {e}")

    # Delete original audio files for the split segment, as the text has changed
    if os.path.exists(audio_dir):
        orig_wav = os.path.join(audio_dir, f"seg{str(num).zfill(5)}.wav")
        if os.path.exists(orig_wav):
            try:
                os.remove(orig_wav)
            except Exception as e:
                print(f"[WARN] Impossible de supprimer {orig_wav}: {e}")

        # Also delete version files of part1
        prefix_orig = f"seg{str(num).zfill(5)}_v"
        for f in os.listdir(audio_dir):
            if f.startswith(prefix_orig) and f.endswith(".wav"):
                try:
                    os.remove(os.path.join(audio_dir, f))
                except Exception as e:
                    print(f"[WARN] Impossible de supprimer la version alternative {f}: {e}")

    if name in segment_cache:
        segment_cache[name] = {}

    return {"message": f"Segment {num} scindé avec succès en {num} et {num + 1}."}


@app.delete("/api/projects/{name}/segments/{num}")
def delete_segment(name: str, num: int):
    """Deletes a segment and shifts subsequent files by -1."""
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(project_dir) or not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")

    book_dir = os.path.join(project_dir, "book")
    tts_dir = os.path.join(project_dir, "tts")
    audio_dir = os.path.join(project_dir, "audio")

    # Find the maximum segment index currently in the project
    max_idx = 0
    if os.path.exists(tts_dir):
        files = [f for f in os.listdir(tts_dir) if f.startswith("seg") and f.endswith(".json")]
        for f in files:
            try:
                val = int(f[3:8])
                if val > max_idx:
                    max_idx = val
            except ValueError:
                pass
    if os.path.exists(book_dir):
        files = [f for f in os.listdir(book_dir) if f.startswith("seg") and f.endswith(".txt")]
        for f in files:
            try:
                val = int(f[3:8])
                if val > max_idx:
                    max_idx = val
            except ValueError:
                pass

    if num < 1 or num > max_idx:
        raise HTTPException(status_code=400, detail="Numéro de segment invalide")

    # 1. Delete files of segment `num`
    del_suffix = str(num).zfill(5)

    # Delete json in tts/
    if os.path.exists(tts_dir):
        target_json = os.path.join(tts_dir, f"seg{del_suffix}.json")
        if os.path.exists(target_json):
            os.remove(target_json)

    # Delete txt in book/
    if os.path.exists(book_dir):
        target_txt = os.path.join(book_dir, f"seg{del_suffix}.txt")
        if os.path.exists(target_txt):
            os.remove(target_txt)

    # Delete wav and version wavs in audio/
    if os.path.exists(audio_dir):
        target_wav = os.path.join(audio_dir, f"seg{del_suffix}.wav")
        if os.path.exists(target_wav):
            os.remove(target_wav)

        prefix_del = f"seg{del_suffix}_v"
        for f in os.listdir(audio_dir):
            if f.startswith(prefix_del) and f.endswith(".wav"):
                os.remove(os.path.join(audio_dir, f))

    # 2. Rename/shift all files from num + 1 up to max_idx in ascending order
    try:
        # Group version wav files by segment suffix to avoid O(N^2) os.listdir calls
        version_files_by_suffix = {}
        if os.path.exists(audio_dir):
            try:
                for f in os.listdir(audio_dir):
                    if f.startswith("seg") and "_v" in f and f.endswith(".wav"):
                        parts = f.split("_v")
                        if len(parts) == 2:
                            prefix = parts[0]
                            suffix = prefix[3:]
                            if suffix not in version_files_by_suffix:
                                version_files_by_suffix[suffix] = []
                            version_files_by_suffix[suffix].append(f)
            except Exception as e:
                print(f"[WARN] Error pre-scanning versions for delete: {e}")

        for i in range(num + 1, max_idx + 1):
            old_suffix = str(i).zfill(5)
            new_suffix = str(i - 1).zfill(5)

            # Rename json in tts/
            if os.path.exists(tts_dir):
                old_json = os.path.join(tts_dir, f"seg{old_suffix}.json")
                new_json = os.path.join(tts_dir, f"seg{new_suffix}.json")
                if os.path.exists(old_json):
                    os.rename(old_json, new_json)

            # Rename txt in book/
            if os.path.exists(book_dir):
                old_txt = os.path.join(book_dir, f"seg{old_suffix}.txt")
                new_txt = os.path.join(book_dir, f"seg{new_suffix}.txt")
                if os.path.exists(old_txt):
                    os.rename(old_txt, new_txt)

            # Rename wav and version wavs in audio/
            if os.path.exists(audio_dir):
                old_wav = os.path.join(audio_dir, f"seg{old_suffix}.wav")
                new_wav = os.path.join(audio_dir, f"seg{new_suffix}.wav")
                if os.path.exists(old_wav):
                    os.rename(old_wav, new_wav)

                # Rename any version wav files (e.g. seg00648_v1.wav -> seg00647_v1.wav)
                prefix_old = f"seg{old_suffix}_v"
                prefix_new = f"seg{new_suffix}_v"
                for f in version_files_by_suffix.get(old_suffix, []):
                    os.rename(
                        os.path.join(audio_dir, f),
                        os.path.join(audio_dir, f.replace(prefix_old, prefix_new, 1))
                    )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors du décalage des fichiers après suppression : {e}")

    if name in segment_cache:
        segment_cache[name] = {}

    return {"message": f"Segment {num} supprimé et fichiers réalignés."}


@app.post("/api/projects/{name}/segments/{num}/qc_score")
async def get_segment_qc_score(name: str, num: int):
    """Recalcule (natif) le score QC d'un segment contre l'empreinte de sa voix.

    Utilise `generated_voice` du segment → bibliothèque d'empreintes → scoring
    corrigé par la durée. Écrit qc_score dans le JSON (peut être None = n/a).
    """
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(project_dir) or not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")

    tts_dir = os.path.join(project_dir, "tts")
    json_name = f"seg{str(num).zfill(5)}.json"
    json_path = os.path.join(tts_dir, json_name)
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Segment introuvable")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            seg_data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lecture du segment impossible : {e}")

    voice_name = resolve_segment_voice_name(name, seg_data)
    # Empreinte créée à la volée depuis VoiceBox si absente.
    voice_id = await asyncio.to_thread(ensure_voiceprint_for_name, voice_name)
    if not voice_id:
        raise HTTPException(status_code=400, detail=f"Aucune empreinte pour la voix '{voice_name}' (échantillon VoiceBox manquant ?).")

    wav_name = f"seg{str(num).zfill(5)}.wav"
    wav_path = os.path.join(project_dir, "audio", wav_name)
    if not os.path.exists(wav_path):
        raise HTTPException(status_code=400, detail="Fichier audio du segment introuvable. Veuillez d'abord le générer.")

    result, err = await qc_score_native(voice_id, wav_path)
    if result is None:
        raise HTTPException(status_code=500, detail=f"Erreur de calcul du score QC : {err}")

    seg_data["qc_score"] = result.get("score")
    seg_data["qc_duration"] = result.get("duration")
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(seg_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Failed to write qc_score to JSON: {e}")

    if name in segment_cache:
        segment_cache[name].pop(json_name, None)

    return {
        "score": result.get("score"),
        "voice": voice_name,
        "duration": result.get("duration"),
        "matched_ref_sec": result.get("matched_ref_sec"),
    }


@app.get("/api/projects/{name}/segments/{num}/versions_qc")
async def versions_qc(name: str, num: int):
    """Score QC de chaque version d'un segment (pour la modal de sélection).

    Note chaque WAV `seg{num}_vN.wav` (+ le principal s'il existe) contre la
    voix du segment, avec correction de durée. Sert à comparer les versions
    à l'oreille + au score.
    """
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")

    tts_dir = os.path.join(project_dir, "tts")
    audio_dir = os.path.join(project_dir, "audio")
    json_path = os.path.join(tts_dir, f"seg{str(num).zfill(5)}.json")

    voice_name = None
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                seg = json.load(f)
            voice_name = resolve_segment_voice_name(name, seg)
        except Exception:
            pass
    voice_id = await asyncio.to_thread(ensure_voiceprint_for_name, voice_name) if voice_name else None

    prefix = f"seg{str(num).zfill(5)}"
    scores = {}
    if voice_id and os.path.isdir(audio_dir):
        for f in sorted(os.listdir(audio_dir)):
            if not f.endswith(".wav"):
                continue
            if f.startswith(prefix + "_v"):
                version = f[len(prefix) + 1:-4]  # "v1", "v2", ...
            elif f == prefix + ".wav":
                version = "main"
            else:
                continue
            result, _err = await qc_score_native(voice_id, os.path.join(audio_dir, f))
            if result is not None:
                scores[version] = {
                    "score": result.get("score"),
                    "duration": result.get("duration"),
                    "matched_ref_sec": result.get("matched_ref_sec"),
                }

    return {"voice": voice_name, "has_voiceprint": bool(voice_id), "scores": scores}


RECOMPUTE_QC_CONCURRENCY = 8  # appels parallèles au service QC (évite le séquentiel sur gros projets)


@app.get("/api/projects/{name}/qc/recompute")
async def recompute_qc(name: str, actor: Optional[str] = Query(None)):
    """Recalcule (natif) le score QC de tous les segments ayant un WAV, en
    streamant la progression en SSE (comme la génération). Traite les segments
    par lots parallèles (RECOMPUTE_QC_CONCURRENCY) plutôt qu'un par un : sur
    un projet de plusieurs milliers de segments, le séquentiel donnait
    l'impression que « rien ne se passe » (aucun retour, plusieurs minutes).

    - Sans `actor` (ou "all") : tous les segments.
    - Avec `actor` : uniquement les segments de ce rôle (profile_id).
    Re-score même les segments déjà notés (les anciens scores étaient faux).
    """
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")

    tts_dir = os.path.join(project_dir, "tts")
    audio_dir = os.path.join(project_dir, "audio")

    actor_filter = None
    if actor and actor.strip().lower() not in ("all", "tous", "tous les acteurs", ""):
        actor_filter = actor.strip()

    async def stream():
        if not os.path.isdir(tts_dir):
            yield "data: [INFO] Aucun segment à évaluer.\n\n"
            yield "data: [INFO] Commande terminée avec le code de sortie : 0\n\n"
            return

        eligible = []
        for f in os.listdir(tts_dir):
            if not (f.startswith("seg") and f.endswith(".json")):
                continue
            try:
                num = int(f[3:8])
            except ValueError:
                continue
            wav = os.path.join(audio_dir, f"seg{str(num).zfill(5)}.wav")
            if not os.path.exists(wav):
                continue
            if actor_filter:
                try:
                    with open(os.path.join(tts_dir, f), "r", encoding="utf-8") as fh:
                        d = json.load(fh)
                    if (d.get("profile_id") or "").strip() != actor_filter:
                        continue
                except Exception:
                    continue
            eligible.append(num)
        eligible.sort()

        total = len(eligible)
        yield f"data: [INFO] Recalcul QC natif : {total} segment(s) ({actor_filter or 'tous les acteurs'})...\n\n"
        if total == 0:
            yield "data: [INFO] Commande terminée avec le code de sortie : 0\n\n"
            return

        # 1) On REFAIT d'abord l'empreinte depuis l'échantillon VoiceBox ACTUEL,
        #    pour chaque voix concernée. Essentiel si une voix a été refaite dans
        #    VoiceBox (échantillon insatisfaisant remplacé) : on re-score les WAV
        #    contre l'empreinte à jour.
        voix = set()
        for num in eligible:
            try:
                with open(os.path.join(tts_dir, f"seg{str(num).zfill(5)}.json"), "r", encoding="utf-8") as fh:
                    sd = json.load(fh)
                vn = sd.get("generated_voice") or sd.get("profile_id")
                if vn:
                    voix.add(vn)
            except Exception:
                pass
        if voix and QC_URL:
            yield f"data: [INFO] Rafraîchissement des empreintes VoiceBox ({len(voix)} voix)...\n\n"
            for vn in sorted(voix):
                try:
                    vid = await asyncio.to_thread(ensure_voiceprint_for_name, vn, True)
                    yield f"data: [INFO] Empreinte {'mise à jour' if vid else 'introuvable'} : {vn}\n\n"
                except Exception as e:
                    yield f"data: [WARN] Empreinte '{vn}' : {e}\n\n"

        sem = asyncio.Semaphore(RECOMPUTE_QC_CONCURRENCY)
        done_count = 0
        done_lock = asyncio.Lock()
        progress_q: asyncio.Queue = asyncio.Queue()

        async def worker(num: int):
            nonlocal done_count
            async with sem:
                try:
                    await run_auto_evaluation(name, num)
                except Exception as e:
                    print(f"[WARN] recompute QC seg {num}: {e}")
            async with done_lock:
                done_count += 1
                await progress_q.put(done_count)

        tasks = [asyncio.create_task(worker(n)) for n in eligible]

        # Émet une ligne de progression toutes les ~25 segments (évite de spammer
        # la console sur un projet de plusieurs milliers de segments).
        report_every = max(1, total // 40)
        last_reported = 0
        pending = asyncio.gather(*tasks)
        while not pending.done():
            try:
                dc = await asyncio.wait_for(progress_q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if dc - last_reported >= report_every or dc == total:
                last_reported = dc
                pct = round(dc * 100 / total)
                yield f"data: [INFO] Recalcul QC : {dc}/{total} ({pct}%)\n\n"
        await pending  # propage une éventuelle exception résiduelle

        if name in segment_cache:
            segment_cache[name] = {}

        yield f"data: [OK] Recalcul QC terminé : {total} segment(s) re-scorés ({actor_filter or 'tous les acteurs'}).\n\n"
        yield "data: [INFO] Commande terminée avec le code de sortie : 0\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


class QcThresholdsUpdate(BaseModel):
    # { "ANTOINE": {"1": 45, "2": 50, "3": 55, "5": 60, "10": 70, "15": 75}, ... }
    qc_thresholds: Dict[str, Dict[str, float]]


class VoiceThresholdsUpdate(BaseModel):
    # Seuils par voix (voice_id) : { "<voice_id>": {"1": 25, ...}, ... }
    voice_thresholds: Dict[str, Dict[str, float]]


@app.put("/api/projects/{name}/qc_thresholds")
def update_qc_thresholds(name: str, data: QcThresholdsUpdate):
    """Enregistre les seuils QC dans le store CENTRAL par voix.

    L'UI envoie les seuils par rôle ; on traduit chaque rôle → voix (via le
    voice_mapping du projet) et on écrit sous voice_id. Ainsi une voix garde
    ses exigences dans tous les projets."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
    try:
        mapping = _project_voice_mapping(name)
        central = load_voice_thresholds()
        applied = {}
        for role, buckets in (data.qc_thresholds or {}).items():
            voice_name = mapping.get(role)
            vid = resolve_voice_id(voice_name) if voice_name else None
            if not vid:
                continue
            central[vid] = buckets
            applied[role] = buckets
        save_voice_thresholds(central)
        if name in segment_cache:
            segment_cache[name] = {}
        return {"message": "Seuils QC enregistrés (centralisés par voix)", "qc_thresholds": applied}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/qc/voice_thresholds")
def update_voice_thresholds_ep(data: VoiceThresholdsUpdate):
    """Enregistre directement les seuils par voix (voice_id) dans le store central."""
    try:
        central = load_voice_thresholds()
        for vid, buckets in (data.voice_thresholds or {}).items():
            if not vid:
                continue
            # Ne garder que les buckets renseignés (valeurs vides = retirées).
            central[vid] = {k: v for k, v in (buckets or {}).items() if v not in (None, "")}
        save_voice_thresholds(central)
        return {"message": "Seuils par voix enregistrés", "count": len(data.voice_thresholds or {})}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/projects/{name}/voice_mapping")
def update_voice_mapping(name: str, data: VoiceMappingUpdate):
    """Updates the voice mapping table in meta.json."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
        
    meta_path = os.path.join(path, "meta.json")
    try:
        meta_data = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
        
        normalized_mapping = {k.replace("’", "'"): v for k, v in data.voice_mapping.items()}
        meta_data["voice_mapping"] = normalized_mapping
        
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=2)
            
        return {"message": "Mapping des voix mis à jour avec succès"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/projects/{name}/custom_characters")
def update_custom_characters(name: str, data: CustomCharactersUpdate):
    """Updates the list of custom characters in meta.json."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
        
    meta_path = os.path.join(path, "meta.json")
    try:
        meta_data = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
        
        # Clean character names: force uppercase, strip, normalize apostrophe, no empty values
        cleaned = {c.strip().upper().replace("’", "'") for c in data.custom_characters if c.strip()}
        
        # Ensure DIDAS is always present if it's a theatre project
        proj_type = meta_data.get("type", "novel")
        if proj_type == "theatre":
            cleaned.add("DIDAS")
            
        cleaned_list = sorted(list(cleaned))
        meta_data["custom_characters"] = cleaned_list
        
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=2)
            
        # Invalidate the memory cache for this project to ensure details GET endpoint loads the updated meta
        if name in segment_cache:
            segment_cache[name] = {}
            
        return {
            "message": "Liste des personnages mise à jour avec succès",
            "custom_characters": cleaned_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/projects/{name}/normalization_rules")
def update_normalization_rules(name: str, data: NormalizeRulesUpdate):
    """Updates the list of custom normalization rules in meta.json."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
        
    meta_path = os.path.join(path, "meta.json")
    try:
        meta_data = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
        
        rules = [r.dict() for r in data.normalization_rules]
        meta_data["normalization_rules"] = rules
        
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=2)
            
        if name in segment_cache:
            segment_cache[name] = {}
            
        return {"message": "Règles de normalisation mises à jour avec succès", "rules": rules}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/projects/{name}/custom_play_styles")
def update_custom_play_styles(name: str, data: PlayStylesUpdate):
    """Updates the list of custom play style presets in the global custom_play_styles.json."""
    try:
        styles = [s.dict() for s in data.custom_play_styles if not s.system]
        global_path = os.path.join(PRESETS_DIR, "custom_play_styles.json")

        with open(global_path, "w", encoding="utf-8") as f:
            json.dump(styles, f, ensure_ascii=False, indent=2)

        if name in segment_cache:
            segment_cache[name] = {}

        return {
            "message": "Styles de formatage des rôles mis à jour globalement avec succès",
            "custom_play_styles": styles
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{name}/source_text_sample")
def get_source_text_sample(name: str):
    """Retrieves the source texts (raw and formatted if available)."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
        
    book_dir = os.path.join(path, "book")
    if not os.path.exists(book_dir):
        return {"text": "", "raw_text": "", "is_formatted": False}
        
    # 1. Find raw source text
    raw_file = None
    if os.path.exists(book_dir):
        for f in os.listdir(book_dir):
            if f.lower().endswith(".txt") and not f.lower().startswith("seg"):
                if not f.lower().endswith("_parsed.txt") and not f.lower().endswith("_chunked.txt") and not f.lower().endswith("_formated.txt"):
                    raw_file = f
                    break
                
    raw_text = ""
    if raw_file:
        try:
            with open(os.path.join(book_dir, raw_file), "r", encoding="utf-8") as f:
                raw_text = f.read()
        except Exception as e:
            print(f"[WARN] Error reading raw source file: {e}")
            
    # 2. Find formatted text if exists
    formated_file = None
    if os.path.exists(book_dir):
        for f in os.listdir(book_dir):
            if f.lower().endswith("_formated.txt"):
                formated_file = f
                break
            
    formated_text = ""
    is_formatted = False
    if formated_file:
        try:
            with open(os.path.join(book_dir, formated_file), "r", encoding="utf-8") as f:
                formated_text = f.read()
            is_formatted = True
        except Exception as e:
            print(f"[WARN] Error reading formatted file: {e}")
            
    return {
        "text": formated_text if is_formatted else raw_text,
        "raw_text": raw_text,
        "is_formatted": is_formatted
    }


class SourceTextUpdateRequest(BaseModel):
    text: str


@app.put("/api/projects/{name}/source_text")
def update_source_text(name: str, data: SourceTextUpdateRequest):
    """Overwrites the raw source text file of the project."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
        
    book_dir = os.path.join(path, "book")
    if not os.path.exists(book_dir):
        os.makedirs(book_dir, exist_ok=True)
        
    raw_file = None
    for f in os.listdir(book_dir):
        if f.lower().endswith(".txt") and not f.lower().startswith("seg"):
            if not f.lower().endswith("_parsed.txt") and not f.lower().endswith("_chunked.txt") and not f.lower().endswith("_formated.txt"):
                raw_file = f
                break
                
    if not raw_file:
        raw_file = f"{name}.txt"
        
    file_path = os.path.join(book_dir, raw_file)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(data.text)
            
        if name in segment_cache:
            segment_cache[name] = {}
            
        return {"message": "Texte source mis à jour avec succès"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{name}/file_sample")
def get_file_sample(name: str, file_type: str = Query(...)):
    """Retrieves the full text of a specific file type in the book directory."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
        
    book_dir = os.path.join(path, "book")
    if not os.path.exists(book_dir):
        return {"text": "", "filename": ""}
        
    src_file = None
    if file_type == "parsed":
        suffix = "_parsed.txt"
    elif file_type == "chunked":
        suffix = "_chunked.txt"
    elif file_type == "formated":
        suffix = "_formated.txt"
    else:
        suffix = None

    if suffix:
        for f in os.listdir(book_dir):
            if f.lower().endswith(suffix):
                src_file = f
                break
    else:
        # Find original raw source txt
        for f in os.listdir(book_dir):
            if f.lower().endswith(".txt") and not f.lower().startswith("seg"):
                if not f.lower().endswith("_parsed.txt") and not f.lower().endswith("_chunked.txt") and not f.lower().endswith("_formated.txt"):
                    src_file = f
                    break
                    
    if not src_file:
        return {"text": "", "filename": ""}
        
    src_path = os.path.join(book_dir, src_file)
    try:
        with open(src_path, "r", encoding="utf-8") as f:
            text = f.read()
        return {"text": text, "filename": src_file}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# IA & Rôles : découpage du roman en chunks à "AInalyser" avec WhoIsSpeakingIA
# =============================================================================

def _find_novel_source_txt(book_dir: str) -> Optional[str]:
    """Trouve le fichier texte source d'un roman dans book/.
    Priorité au texte normalisé (*_formated.txt) puis au source brut.
    Ignore les seg*, *_parsed.txt, *_chunked.txt."""
    if not os.path.exists(book_dir):
        return None
    txts = [f for f in os.listdir(book_dir) if f.lower().endswith(".txt")
            and not f.lower().startswith("seg")]
    formated = [f for f in txts if f.lower().endswith("_formated.txt")]
    if formated:
        return formated[0]
    plain = [f for f in txts if not f.lower().endswith(("_parsed.txt", "_chunked.txt"))]
    # Écarter les sauvegardes/copies manuelles (GPsave.txt, *_backup.txt, ...).
    _BACKUP = ("save", "backup", "copy", "copie", "bak", "old")
    non_backup = [f for f in plain if not any(k in f.lower() for k in _BACKUP)]
    candidats = non_backup or plain
    if not candidats:
        return None
    # À défaut d'indice, le vrai livre est le plus gros fichier.
    return max(candidats, key=lambda f: os.path.getsize(os.path.join(book_dir, f)))


def _chunk_novel_for_ai(text: str, min_chars: int = 1500, max_chars: int = 2000) -> List[str]:
    """Découpe le texte en chunks de min..max caractères SANS jamais couper une
    phrase. On regroupe des paragraphes (séparés par des lignes vides) ; un
    paragraphe trop long est re-découpé sur les fins de phrase (. ! ? …)."""
    import re
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) <= 1:
        # texte "aéré" en une phrase par ligne (pas de vraies lignes vides)
        paras = [l.strip() for l in text.splitlines() if l.strip()]

    def split_paragraphe(p: str) -> List[str]:
        phrases = re.split(r"(?<=[.!?…»\"])\s+", p)
        out, buf = [], ""
        for s in phrases:
            if not buf:
                buf = s
            elif len(buf) + 1 + len(s) <= max_chars:
                buf += " " + s
            else:
                out.append(buf)
                buf = s
        if buf:
            out.append(buf)
        return out

    unites: List[str] = []
    for p in paras:
        if len(p) > max_chars:
            unites.extend(split_paragraphe(p))
        else:
            unites.append(p)

    chunks: List[str] = []
    buf = ""
    for u in unites:
        if not buf:
            buf = u
        elif len(buf) + 2 + len(u) <= max_chars:
            buf += "\n\n" + u
        else:
            chunks.append(buf)
            buf = u
    if buf:
        chunks.append(buf)
    return chunks


def _ai_chunks_dir(name: str) -> str:
    return os.path.join(PROJECTS_DIR, name, "book", "ai_chunks")


def _analyzed_name(index: int) -> str:
    return f"chunk_{str(index).zfill(5)}_analyzed.txt"


def _chunk_name(index: int) -> str:
    return f"chunk_{str(index).zfill(5)}.txt"


def _list_ai_chunks(name: str) -> List[dict]:
    d = _ai_chunks_dir(name)
    if not os.path.exists(d):
        return []
    # Fichiers chunk_NNNNN.txt UNIQUEMENT (on exclut les *_analyzed.txt).
    files = sorted(f for f in os.listdir(d)
                   if re.fullmatch(r"chunk_\d+\.txt", f.lower()))
    out = []
    for f in files:
        try:
            with open(os.path.join(d, f), "r", encoding="utf-8") as fh:
                txt = fh.read()
        except Exception:
            txt = ""
        idx = int(re.search(r"\d+", f).group())
        preview = " ".join(txt.split())[:140]
        out.append({
            "index": idx,
            "filename": f,
            "chars": len(txt),
            "preview": preview,
            "analyzed": os.path.exists(os.path.join(d, _analyzed_name(idx))),
        })
    return out


@app.post("/api/projects/{name}/ai_roles/chunk")
def ai_roles_chunk(name: str, min_chars: int = Query(1500), max_chars: int = Query(2000)):
    """Découpe le roman en petits fichiers (chunk_00001.txt, ...) dans
    book/ai_chunks/ en vue de l'analyse d'attribution des rôles par l'IA."""
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")
    book_dir = os.path.join(project_dir, "book")
    src = _find_novel_source_txt(book_dir)
    if not src:
        raise HTTPException(status_code=400, detail="Aucun fichier texte source trouvé dans book/.")

    with open(os.path.join(book_dir, src), "r", encoding="utf-8") as f:
        texte = f.read()

    chunks = _chunk_novel_for_ai(texte, min_chars=min_chars, max_chars=max_chars)
    if not chunks:
        raise HTTPException(status_code=400, detail="Le texte source est vide.")

    out_dir = _ai_chunks_dir(name)
    # On repart d'un dossier propre pour éviter de mélanger d'anciens découpages.
    if os.path.exists(out_dir):
        for old in os.listdir(out_dir):
            if old.lower().startswith("chunk_") and old.lower().endswith(".txt"):
                try:
                    os.remove(os.path.join(out_dir, old))
                except Exception:
                    pass
    os.makedirs(out_dir, exist_ok=True)

    for i, c in enumerate(chunks, start=1):
        with open(os.path.join(out_dir, f"chunk_{str(i).zfill(5)}.txt"), "w", encoding="utf-8") as f:
            f.write(c.strip() + "\n")

    return {
        "source_file": src,
        "count": len(chunks),
        "min_chars": min_chars,
        "max_chars": max_chars,
        "chunks": _list_ai_chunks(name),
    }


@app.get("/api/projects/{name}/ai_roles/chunks")
def ai_roles_list(name: str):
    """Liste les chunks IA déjà générés pour le projet."""
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")
    return {"count": len(_list_ai_chunks(name)), "chunks": _list_ai_chunks(name)}


@app.get("/api/projects/{name}/ai_roles/chunks/{index}")
def ai_roles_get_chunk(name: str, index: int):
    """Renvoie le contenu d'un chunk IA (1-indexé) + son résultat analysé si présent."""
    d = _ai_chunks_dir(name)
    path = os.path.join(d, _chunk_name(index))
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Chunk introuvable")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    analyzed_text = None
    apath = os.path.join(d, _analyzed_name(index))
    if os.path.exists(apath):
        with open(apath, "r", encoding="utf-8") as f:
            analyzed_text = f.read()
    return {
        "index": index,
        "filename": _chunk_name(index),
        "text": text,
        "analyzed": analyzed_text is not None,
        "analyzed_filename": _analyzed_name(index) if analyzed_text is not None else None,
        "analyzed_text": analyzed_text,
    }


class AiNarratorBody(BaseModel):
    text: Optional[str] = None


@app.post("/api/projects/{name}/ai_roles/chunks/{index}/narrator")
def ai_roles_mark_narrator(name: str, index: int, body: Optional[AiNarratorBody] = None):
    """Marque un chunk comme 100% NARRATEUR (sans IA) : crée
    chunk_NNNNN_analyzed.txt = 'NARRATEUR\\n\\n' + texte du chunk.
    Si body.text est fourni (cadre AVANT corrigé), on réécrit d'abord le chunk."""
    d = _ai_chunks_dir(name)
    path = os.path.join(d, _chunk_name(index))
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Chunk introuvable")

    # Corrections éventuelles depuis le cadre AVANT éditable -> on réécrit le chunk.
    if body is not None and body.text is not None and body.text.strip():
        with open(path, "w", encoding="utf-8") as f:
            f.write(body.text.strip() + "\n")

    with open(path, "r", encoding="utf-8") as f:
        texte = f.read().strip()

    analyzed = "NARRATEUR\n\n" + texte + "\n"
    apath = os.path.join(d, _analyzed_name(index))
    with open(apath, "w", encoding="utf-8") as f:
        f.write(analyzed)

    return {
        "index": index,
        "filename": _chunk_name(index),
        "analyzed_filename": _analyzed_name(index),
        "analyzed_text": analyzed,
        "speakers": ["NARRATEUR"],
    }


def _extraire_locuteurs(rendu: str) -> List[str]:
    """Extrait les noms de locuteurs (lignes MAJUSCULES) du rendu, hors NARRATEUR."""
    speakers = []
    for ligne in rendu.split("\n"):
        l = ligne.strip()
        # NARRATEUR et le placeholder générique PERSONNAGE ne sont pas des
        # personnages nommés : on ne les fait pas remonter dans la distribution.
        if not l or l in ("NARRATEUR", "PERSONNAGE"):
            continue
        if l.isupper() and len(l) < 50 and not any(c in l for c in ".!?«»\"–—{}"):
            if l not in speakers:
                speakers.append(l)
    return speakers


def _ai_seen_characters(name: str) -> List[str]:
    """Locuteurs nommés 'vus' dans les analyses enregistrées (chunk_*_analyzed.txt).
    Sert à marquer un personnage comme 'Vu' même s'il n'est pas encore dans les TTS."""
    d = _ai_chunks_dir(name)
    if not os.path.exists(d):
        return []
    seen = set()
    for f in os.listdir(d):
        if re.fullmatch(r"chunk_\d+_analyzed\.txt", f.lower()):
            try:
                with open(os.path.join(d, f), "r", encoding="utf-8") as fh:
                    seen.update(_extraire_locuteurs(fh.read()))
            except Exception:
                pass
    return sorted(seen)


def _ajouter_custom_characters(name: str, speakers: List[str]) -> List[str]:
    """Ajoute les locuteurs détectés à custom_characters de meta.json (dédupliqué)."""
    if not speakers:
        return []
    meta_path = os.path.join(PROJECTS_DIR, name, "meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    existing = list(meta.get("custom_characters", []))
    ajouts = [s for s in speakers if s not in existing]
    if ajouts:
        meta["custom_characters"] = existing + ajouts
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    return ajouts


class AiAnalyzedBody(BaseModel):
    text: str


@app.put("/api/projects/{name}/ai_roles/chunks/{index}/analyzed")
def ai_roles_save_analyzed(name: str, index: int, body: AiAnalyzedBody):
    """Enregistre (validation manuelle) le contenu corrigé du cadre APRÈS dans
    chunk_NNNNN_analyzed.txt, et refait remonter les personnages détectés."""
    d = _ai_chunks_dir(name)
    if not os.path.exists(os.path.join(d, _chunk_name(index))):
        raise HTTPException(status_code=404, detail="Chunk introuvable")
    contenu = (body.text or "").strip()
    if not contenu:
        raise HTTPException(status_code=400, detail="Le contenu analysé est vide.")

    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, _analyzed_name(index)), "w", encoding="utf-8") as f:
        f.write(contenu + "\n")

    speakers = _extraire_locuteurs(contenu)
    ajoutes = _ajouter_custom_characters(name, speakers)
    return {
        "index": index,
        "analyzed_filename": _analyzed_name(index),
        "analyzed_text": contenu + "\n",
        "speakers": speakers,
        "added_characters": ajoutes,
    }


@app.post("/api/projects/{name}/ai_roles/concat")
def ai_roles_concat(name: str):
    """Concatène tous les chunks VALIDÉS (chunk_NNNNN_analyzed.txt), dans l'ordre,
    en un unique <base>_parsed.txt (format identique au parsing théâtre : blocs
    'LOCUTEUR' + texte). Ce fichier peut ensuite être chunké/splitté comme une
    pièce pour fabriquer les segments du tableau central."""
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")
    book_dir = os.path.join(project_dir, "book")
    d = _ai_chunks_dir(name)

    chunks = _list_ai_chunks(name)
    if not chunks:
        raise HTTPException(status_code=400, detail="Aucun chunk. Ouvre IA & Rôles pour les générer.")

    parts, missing = [], []
    for c in sorted(chunks, key=lambda x: x["index"]):
        apath = os.path.join(d, _analyzed_name(c["index"]))
        if os.path.exists(apath):
            with open(apath, "r", encoding="utf-8") as f:
                txt = f.read().strip()
            if txt:
                parts.append(txt)
        else:
            missing.append(c["index"])

    if not parts:
        raise HTTPException(status_code=400, detail="Aucun chunk validé (analyse) à assembler.")

    src = _find_novel_source_txt(book_dir)
    base = os.path.splitext(src)[0] if src else name
    out_name = f"{base}_parsed.txt"
    with open(os.path.join(book_dir, out_name), "w", encoding="utf-8") as f:
        f.write("\n\n".join(parts) + "\n")

    return {
        "file": out_name,
        "included": len(parts),
        "total": len(chunks),
        "missing": missing,
    }


def _run_whoisspeaking_analysis(name: str, index: int) -> dict:
    """Exécute (SYNCHRONE, bloquant) l'analyse d'attribution d'un chunk via
    tools/AI/WhoIsSpeakingIA.py. Lève une exception en cas d'échec. À appeler
    dans un thread (asyncio.to_thread) depuis le worker asynchrone."""
    import subprocess

    d = _ai_chunks_dir(name)
    path = os.path.join(d, _chunk_name(index))
    if not os.path.exists(path):
        raise RuntimeError("Chunk introuvable")

    script = os.path.join(TOOLS_DIR, "AI", "WhoIsSpeakingIA.py")
    if not os.path.exists(script):
        raise RuntimeError("WhoIsSpeakingIA.py introuvable dans tools/AI/.")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("LMSTUDIO_URL", "http://host.docker.internal:1234/v1/chat/completions")

    try:
        res = subprocess.run(
            [PYTHON_EXE, script, path],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=900,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("L'analyse IA a expiré (LLM trop lent ou injoignable).")

    if res.returncode != 0:
        tail = (res.stderr or res.stdout or "").strip()[-500:]
        raise RuntimeError(f"Échec de l'analyse (LM Studio joignable ?). {tail}")

    produced = os.path.join(d, f"{os.path.splitext(_chunk_name(index))[0]}_ai.txt")
    if not os.path.exists(produced):
        raise RuntimeError("L'analyse n'a produit aucun fichier de sortie.")
    with open(produced, "r", encoding="utf-8") as f:
        rendu = f.read().strip()
    try:
        os.remove(produced)
    except Exception:
        pass

    analyzed = rendu + "\n"
    with open(os.path.join(d, _analyzed_name(index)), "w", encoding="utf-8") as f:
        f.write(analyzed)

    speakers = _extraire_locuteurs(rendu)
    ajoutes = _ajouter_custom_characters(name, speakers)
    return {
        "analyzed_filename": _analyzed_name(index),
        "analyzed_text": analyzed,
        "speakers": speakers,
        "added_characters": ajoutes,
    }


# --- File d'analyse IA ASYNCHRONE (sérialisée : 1 appel LLM à la fois) --------
# L'analyse ne bloque plus la requête HTTP (donc plus de dépendance au timeout
# d'un reverse proxy) : on met en file, un worker unique traite un chunk à la
# fois (le GPU ne fait qu'un LLM à la fois), et le front interroge le statut.
_ai_status: Dict[str, dict] = {}          # "projet\x00index" -> {status, error, speakers, added}
_ai_queue: "Optional[asyncio.Queue]" = None
_ai_worker: "Optional[asyncio.Task]" = None


def _ai_key(name: str, index: int) -> str:
    return f"{name}\x00{index}"


async def _ai_analysis_worker():
    """Traite la file d'analyses IA, une à la fois."""
    global _ai_worker
    try:
        while True:
            name, index = await _ai_queue.get()
            key = _ai_key(name, index)
            _ai_status[key] = {"status": "running", "updated": time.time()}
            try:
                result = await asyncio.to_thread(_run_whoisspeaking_analysis, name, index)
                _ai_status[key] = {"status": "done", "updated": time.time(),
                                   "speakers": result["speakers"],
                                   "added_characters": result["added_characters"]}
                if name in segment_cache:
                    segment_cache[name] = {}
            except Exception as e:
                _ai_status[key] = {"status": "error", "updated": time.time(), "error": str(e)}
            finally:
                _ai_queue.task_done()
    except asyncio.CancelledError:
        pass
    finally:
        _ai_worker = None


def _enqueue_analysis(name: str, index: int):
    """Met un chunk en file d'analyse et (re)démarre le worker au besoin."""
    global _ai_queue, _ai_worker
    if _ai_queue is None:
        _ai_queue = asyncio.Queue()
    _ai_status[_ai_key(name, index)] = {"status": "queued", "updated": time.time()}
    _ai_queue.put_nowait((name, index))
    if _ai_worker is None or _ai_worker.done():
        _ai_worker = asyncio.create_task(_ai_analysis_worker())


@app.post("/api/projects/{name}/ai_roles/chunks/{index}/analyze")
async def ai_roles_analyze(name: str, index: int, body: Optional[AiNarratorBody] = None):
    """Met le chunk EN FILE d'analyse IA et rend la main IMMÉDIATEMENT (asynchrone).
    Le front suit ensuite l'avancement via .../ai_roles/analyze_status.
    NB : endpoint `async` obligatoire — `_enqueue_analysis` crée une tâche asyncio,
    ce qui exige de tourner dans la boucle d'événements (pas un thread sync)."""
    d = _ai_chunks_dir(name)
    path = os.path.join(d, _chunk_name(index))
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Chunk introuvable")

    # Corrections éventuelles du cadre AVANT -> on réécrit le chunk avant analyse.
    if body is not None and body.text is not None and body.text.strip():
        with open(path, "w", encoding="utf-8") as f:
            f.write(body.text.strip() + "\n")

    _enqueue_analysis(name, index)
    return {"index": index, "status": "queued"}


@app.get("/api/projects/{name}/ai_roles/analyze_status")
def ai_roles_analyze_status(name: str):
    """Statuts d'analyse en cours/terminés pour ce projet : { index: {status,...} }.
    Le front poll cet endpoint (petites requêtes → insensible au reverse proxy)."""
    prefix = f"{name}\x00"
    jobs = {}
    active = 0
    for key, val in _ai_status.items():
        if key.startswith(prefix):
            idx = int(key.split("\x00", 1)[1])
            jobs[idx] = val
            if val.get("status") in ("queued", "running"):
                active += 1
    return {"jobs": jobs, "active": active}


@app.post("/api/projects/{name}/run_tool_sync")
def run_tool_sync(name: str, action: str = Query(...)):
    """Executes a pipeline tool script synchronously and returns the output/code."""
    import subprocess
    cmd = []
    
    if action == "parse_theatre":
        book_dir = os.path.join(PROJECTS_DIR, name, "book")
        src_file = None
        if os.path.exists(book_dir):
            for f in os.listdir(book_dir):
                if f.lower().endswith("_formated.txt"):
                    src_file = f
                    break
        if not src_file and os.path.exists(book_dir):
            for f in os.listdir(book_dir):
                if f.lower().endswith(".txt") and not f.lower().startswith("seg"):
                    if not f.lower().endswith("_parsed.txt") and not f.lower().endswith("_chunked.txt"):
                        src_file = f
                        break
        
        if not src_file:
            raise HTTPException(status_code=400, detail="Aucun fichier source brut trouvé à parser.")
            
        src_path = os.path.join(book_dir, src_file)
        if src_file.endswith("_formated.txt"):
            dest_file = src_file.replace("_formated.txt", "_parsed.txt")
        else:
            dest_file = src_file.replace(".txt", "_parsed.txt")
        dest_path = os.path.join(book_dir, dest_file)
        
        script_path = os.path.join(TOOLS_DIR, "parse_theatre.py")
        cmd = [PYTHON_EXE, script_path, src_path, dest_path]
        
    elif action == "chunk_theatre":
        book_dir = os.path.join(PROJECTS_DIR, name, "book")
        parsed_file = None
        if os.path.exists(book_dir):
            for f in os.listdir(book_dir):
                if f.lower().endswith("_parsed.txt"):
                    parsed_file = f
                    break
                    
        if not parsed_file:
            raise HTTPException(status_code=400, detail="Aucun fichier de théâtre parsé (*_parsed.txt) trouvé.")
            
        src_path = os.path.join(book_dir, parsed_file)
        dest_path = os.path.join(book_dir, parsed_file.replace("_parsed.txt", "_chunked.txt"))
        
        script_path = os.path.join(TOOLS_DIR, "chunker_theatre.py")
        cmd = [PYTHON_EXE, script_path, src_path, dest_path]
        
    elif action == "split":
        project_dir = os.path.join(PROJECTS_DIR, name)
        meta_path = os.path.join(project_dir, "meta.json")
        project_type = "novel"
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                project_type = meta_data.get("type", "novel")
            except Exception:
                pass
        else:
            book_dir = os.path.join(project_dir, "book")
            has_parsed_file = False
            if os.path.exists(book_dir):
                has_parsed_file = any(f.lower().endswith("_parsed.txt") for f in os.listdir(book_dir))
            project_type = "theatre" if has_parsed_file else "novel"
            
        if project_type == "theatre":
            script_path = os.path.join(TOOLS_DIR, "split_theatre.py")
            cmd = [PYTHON_EXE, script_path, name]
        else:
            script_path = os.path.join(TOOLS_DIR, "split_book.py")
            cmd = [PYTHON_EXE, script_path, name]
            
    else:
        raise HTTPException(status_code=400, detail="Action inconnue ou non prise en charge en mode synchrone")

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Erreur d'exécution : {res.stderr or res.stdout}")
        return {"output": res.stdout, "returncode": res.returncode}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def make_replacement_callback(replace_str: str):
    """
    Creates a callback function for re.sub that dynamically interpolates
    captured groups (like $1, $2 or \1, \2) and supports newlines.
    Using a callback prevents Python's re.sub from throwing 'bad escape' errors
    on unescaped literal backslashes (like \P in \PHILAMINTE).
    """
    if not replace_str:
        return lambda m: ""
    import re
    # Translate literal backslash+n to actual newline
    adapted_replace = replace_str.replace("\\n", "\n")
    
    def callback(match_obj):
        result = adapted_replace
        
        # 1. Replace JS style $1, $2, etc.
        def replace_group_js(m):
            group_num = int(m.group(1))
            try:
                val = match_obj.group(group_num)
                return val if val is not None else ""
            except IndexError:
                return m.group(0)
                
        result = re.sub(r'\$(\d+)', replace_group_js, result)
        
        # 2. Replace Python style \1, \2, etc.
        def replace_group_py(m):
            group_num = int(m.group(1))
            try:
                val = match_obj.group(group_num)
                return val if val is not None else ""
            except IndexError:
                return m.group(0)
                
        result = re.sub(r'\\(\d+)', replace_group_py, result)
        return result
        
    return callback


def compile_js_regex(regex_str: str):
    """
    Parses a JS-style regex string /pattern/flags and returns a compiled re.Pattern object.
    If it's not a JS-style regex, tries to compile as a regex first, falling back to literal escape.
    """
    import re
    if regex_str.startswith("/") and "/" in regex_str[1:]:
        last_slash_idx = regex_str.rfind("/")
        pattern = regex_str[1:last_slash_idx]
        flags_str = regex_str[last_slash_idx+1:]
        
        flags = 0
        if "i" in flags_str:
            flags |= re.IGNORECASE
        if "m" in flags_str:
            flags |= re.MULTILINE
        return re.compile(pattern, flags)
    else:
        # Try compiling as a regex first. If it's valid, use it; otherwise, escape as literal.
        try:
            return re.compile(regex_str)
        except re.error:
            return re.compile(re.escape(regex_str))


def apply_normalization(text: str, remove_page_numbers: bool, piece_style: Union[str, Dict[str, Any]], actors: List[str], rules: List[dict] = None) -> str:
    if not text:
        return ""
    
    # Standardize piece_style to a dictionary configuration
    DEFAULT_STYLES = {
        "modern": {
            "id": "modern",
            "name": "Moderne (Eva (dida) – dialogue)",
            "pattern": r"^__ACTORS__\s*(?:\(([^)]*)\))?\s*[-–\—]\s*(.*)$"
        },
        "classic": {
            "id": "classic",
            "name": "Classique avec Point (JEANNE. dialogue)",
            "pattern": r"^__ACTORS__\s*\.\s*(.*)$"
        },
        "moliere": {
            "id": "moliere",
            "name": "Alternance Simple (Dorine\ndialogue)",
            "pattern": r"^\s*__ACTORS__\s*$"
        }
    }
    
    style_config = None
    if isinstance(piece_style, str):
        style_config = DEFAULT_STYLES.get(piece_style)
        if not style_config and piece_style == "none":
            style_config = {"id": "none", "name": "None", "pattern": ""}
    elif isinstance(piece_style, dict):
        style_config = piece_style
        
    if not style_config:
        # Fallback to modern
        style_config = DEFAULT_STYLES["modern"]
        
    # When piece_style is 'none', skip all formatting/merging and only apply custom rules
    if style_config.get("id") == "none":
        import re
        formatted_lines = text.splitlines()
        # Retrait des numéros de page (lignes purement numériques ou « - 12 - » /
        # « p. 12 ») — utile pour un roman issu de PDF/EPUB.
        if remove_page_numbers:
            kept = []
            for l in formatted_lines:
                s = l.strip()
                if re.match(r"^\d+$", s):
                    continue
                if re.match(r"^[-–—]?\s*\d+\s*[-–—]?$", s):
                    continue
                if re.match(r"^(page|p\.)\s*\d+$", s, re.IGNORECASE):
                    continue
                kept.append(l)
            formatted_lines = kept
        if rules:
            processed_lines = []
            for line in formatted_lines:
                current_line = line
                had_text = bool(line.strip())
                for rule in rules:
                    if not rule.get("active", True):
                        continue
                    search_pat = rule.get("search", "")
                    if not search_pat:
                        continue
                    replace_str = rule.get("replace", "")
                    try:
                        compiled = compile_js_regex(search_pat)
                        current_line = compiled.sub(make_replacement_callback(replace_str), current_line)
                    except Exception as e:
                        print(f"[WARN] Error applying regex '{search_pat}': {e}")
                
                if had_text and not current_line.strip():
                    continue
                    
                processed_lines.append(current_line)
            
            # Flatten lines that might contain newlines from regex replacements
            flattened_lines = []
            for pl in processed_lines:
                flattened_lines.extend(pl.splitlines() if pl else [""])
            formatted_lines = flattened_lines
        
        # Collapse multiple consecutive empty lines
        result = []
        prev_empty = False
        for l in formatted_lines:
            if l == "":
                if not prev_empty:
                    result.append("")
                prev_empty = True
            else:
                result.append(l)
                prev_empty = False
        return "\n".join(result)
        
    lines = text.splitlines()
    actors_upper = [a.strip().upper() for a in actors if a.strip()]
    if not actors_upper:
        actors_upper = ["EVA", "ALBAN", "JEANNE", "SIMON", "RALPH", "DORINE", "ORGON", "DIDAS"]
        
    import re
    
    # Apply custom normalization rules on raw lines BEFORE merging/formatting
    if rules:
        processed_raw_lines = []
        for line in lines:
            current_line = line
            had_text = bool(line.strip())
            for rule in rules:
                if not rule.get("active", True):
                    continue
                search_pat = rule.get("search", "")
                if not search_pat:
                    continue
                replace_str = rule.get("replace", "")
                try:
                    compiled = compile_js_regex(search_pat)
                    current_line = compiled.sub(make_replacement_callback(replace_str), current_line)
                except Exception as e:
                    print(f"[WARN] Error pre-applying raw regex '{search_pat}': {e}")
            if had_text and not current_line.strip():
                continue
            processed_raw_lines.append(current_line)
            
        flattened_raw = []
        for prl in processed_raw_lines:
            flattened_raw.extend(prl.splitlines() if prl else [""])
        lines = flattened_raw

    # 1. Clean and remove page numbers
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if remove_page_numbers:
            if re.match(r"^\d+$", stripped):
                continue
            if re.match(r"^\d+\.\s+.*$", stripped):
                continue
        cleaned_lines.append(stripped)
        
    def make_actor_regex_pattern(actor: str) -> str:
        esc = re.escape(actor)
        return esc.replace(r"\'", "['’]").replace(r"\’", "['’]").replace("'", "['’]").replace("’", "['’]")

    # Compile the active style's regex dynamically
    style_pattern = style_config.get("pattern", "")
    actors_pattern = "|".join([make_actor_regex_pattern(a) for a in actors_upper])
    raw_pattern = style_pattern.replace("__ACTORS__", f"({actors_pattern})").replace("$ACTORS", f"({actors_pattern})")
    
    try:
        compiled_regex = re.compile(raw_pattern, re.IGNORECASE)
    except Exception as e:
        print(f"[WARN] Failed to compile play style regex '{raw_pattern}': {e}")
        # fallback to modern style pattern
        compiled_regex = re.compile(rf"^({actors_pattern})\s*(?:\(([^)]*)\))?\s*[-–\—]\s*(.*)$", re.IGNORECASE)

    # Helper to check if a line starts a new speech
    def starts_new_speech(s_line: str) -> bool:
        if not s_line:
            return False
        return bool(compiled_regex.match(s_line))

    # Merge lines
    merged = []
    for line in cleaned_lines:
        if not line:
            merged.append("")
            continue
            
        if not merged:
            merged.append(line)
            continue
            
        prev = merged[-1]
        can_merge = False
        if prev != "":
            if not starts_new_speech(line):
                # If previous line was just an actor name (Molière-like: 1 capture group)
                if compiled_regex.groups == 1:
                    prev_norm = prev.upper().replace("’", "'")
                    actors_norm = [a.replace("’", "'") for a in actors_upper]
                    if prev_norm in actors_norm:
                        can_merge = False
                    else:
                        can_merge = True
                else:
                    can_merge = True
                    
        if can_merge:
            merged[-1] = prev + " " + line
        else:
            merged.append(line)
            
    # Apply play style formatting on merged lines
    formatted_lines = []
    i = 0
    while i < len(merged):
        line = merged[i]
        if not line:
            formatted_lines.append("")
            i += 1
            continue
            
        matched = False
        match = compiled_regex.match(line)
        if match:
            groups = match.groups()
            name_val = groups[0] if len(groups) >= 1 else None
            
            if name_val:
                if formatted_lines and formatted_lines[-1] != "":
                    formatted_lines.append("")
                formatted_lines.append(name_val.upper().replace("’", "'"))
                formatted_lines.append("")
                
                # Check how many groups were captured to adapt formatting
                if len(groups) >= 3:
                    # modern-like: Actor (group 1), Didascalie (group 2), Dialogue (group 3)
                    didas = groups[1]
                    dialogue = groups[2]
                    if didas:
                        formatted_lines.append(f"({didas.strip()}) {dialogue.strip()}")
                    else:
                        formatted_lines.append(dialogue.strip())
                elif len(groups) == 2:
                    # classic-like: Actor (group 1), Dialogue (group 2)
                    dialogue = groups[1]
                    formatted_lines.append(dialogue.strip())
                # if len(groups) == 1, it's moliere-like (only Actor name, no dialogue parsed from this line)
                
                formatted_lines.append("")
                matched = True

        if not matched:
            formatted_lines.append(line)
            
        i += 1
        
    # Apply custom normalization rules on the final formatted lines
    if rules:
        processed_lines = []
        for line in formatted_lines:
            current_line = line
            had_text = bool(line.strip())
            for rule in rules:
                if not rule.get("active", True):
                    continue
                search_pat = rule.get("search", "")
                if not search_pat:
                    continue
                replace_str = rule.get("replace", "")
                try:
                    compiled = compile_js_regex(search_pat)
                    current_line = compiled.sub(make_replacement_callback(replace_str), current_line)
                except Exception as e:
                    print(f"[WARN] Error applying regex '{search_pat}': {e}")
            
            # If the line had text but is now empty after applying rules, discard it completely
            if had_text and not current_line.strip():
                continue
                
            processed_lines.append(current_line)
            
        # Since custom rules might introduce newlines, we flatten the lines list
        flattened_lines = []
        for pl in processed_lines:
            flattened_lines.extend(pl.splitlines() if pl else [""])
        formatted_lines = flattened_lines
        
    # Collapse multiple consecutive empty lines
    result = []
    prev_empty = False
    for l in formatted_lines:
        if l == "":
            if not prev_empty:
                result.append("")
            prev_empty = True
        else:
            result.append(l)
            prev_empty = False
            
    return "\n".join(result)


@app.post("/api/projects/{name}/normalize/preview")
def normalize_preview(name: str, request: NormalizePreviewRequest):
    """Simulates/applies normalization rules on a sample text using Python regex."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
        
    meta_path = os.path.join(path, "meta.json")
    custom_chars = ["DIDAS"]
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
            custom_chars = meta_data.get("custom_characters", ["DIDAS"])
            if "DIDAS" not in custom_chars:
                custom_chars.append("DIDAS")
        except Exception:
            pass
            
    actors = [c.strip().upper() for c in custom_chars if c.strip()]
    if not actors:
        actors = ["DIDAS"]
        
    rules = [r.dict() for r in request.normalization_rules] if request.normalization_rules else []
        
    normalized = apply_normalization(
        request.text,
        request.remove_page_numbers,
        request.piece_style,
        actors,
        rules
    )
    return {"normalized_text": normalized}


@app.post("/api/projects/{name}/normalize/apply")
def normalize_apply(name: str, request: NormalizeApplyRequest):
    """Applies normalization rules on the entire source play text and writes to _formated.txt."""
    path = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Projet introuvable")
        
    book_dir = os.path.join(path, "book")
    if not os.path.exists(book_dir):
        raise HTTPException(status_code=400, detail="Dossier book introuvable pour ce projet")
        
    # Find original raw source txt (not chunked, parsed, or formatted)
    src_file = None
    for f in os.listdir(book_dir):
        if f.lower().endswith(".txt") and not f.lower().startswith("seg"):
            if not f.lower().endswith("_parsed.txt") and not f.lower().endswith("_chunked.txt") and not f.lower().endswith("_formated.txt"):
                src_file = f
                break
                
    if not src_file:
        raise HTTPException(status_code=400, detail="Aucun fichier texte source brut trouvé dans /book")
        
    src_path = os.path.join(book_dir, src_file)
    
    # Load custom characters & rules
    meta_path = os.path.join(path, "meta.json")
    custom_chars = ["DIDAS"]
    rules = []
    
    try:
        meta_data = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
                
        if request.normalization_rules is not None:
            rules = [r.dict() for r in request.normalization_rules]
            meta_data["normalization_rules"] = rules
        else:
            rules = meta_data.get("normalization_rules", [])
            
        meta_data["piece_style"] = request.piece_style
        meta_data["remove_page_numbers"] = request.remove_page_numbers
        custom_chars = meta_data.get("custom_characters", ["DIDAS"])
        
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Error saving/loading normalization options from meta.json: {e}")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                custom_chars = meta_data.get("custom_characters", ["DIDAS"])
                rules = meta_data.get("normalization_rules", [])
            except Exception:
                pass

    if "DIDAS" not in custom_chars:
        custom_chars.append("DIDAS")
            
    actors = [c.strip().upper() for c in custom_chars if c.strip()]
    if not actors:
        actors = ["DIDAS"]
        
    if request.overwrite_source:
        dest_name = src_file
        dest_path = src_path
    else:
        dest_name = src_file.replace(".txt", "_formated.txt")
        dest_path = os.path.join(book_dir, dest_name)

    if request.text is not None:
        try:
            with open(dest_path, "w", encoding="utf-8") as f:
                f.write(request.text)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur lors de l'écriture du fichier normalisé : {e}")
    else:
        try:
            with open(src_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur lors de la lecture du fichier source : {e}")
        
        normalized = apply_normalization(
            text,
            request.remove_page_numbers,
            request.piece_style,
            actors,
            rules
        )
        
        try:
            with open(dest_path, "w", encoding="utf-8") as f:
                f.write(normalized)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur lors de l'écriture du fichier normalisé : {e}")
        
    # Invalidate cache
    if name in segment_cache:
        segment_cache[name] = {}
        
    return {"message": f"Normalisation appliquée et sauvegardée sous {dest_name}", "filename": dest_name}


@app.get("/api/presets/normalization")
def get_normalization_presets():
    """Retrieve all saved normalization presets."""
    presets = []
    if not os.path.exists(NORMALIZATION_PRESETS_DIR):
        return presets
    for filename in os.listdir(NORMALIZATION_PRESETS_DIR):
        if filename.endswith(".json"):
            name = filename[:-5]
            preset_path = os.path.join(NORMALIZATION_PRESETS_DIR, filename)
            try:
                with open(preset_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                presets.append({"name": name, "config": config})
            except Exception as e:
                print(f"[WARN] Failed to load preset {filename}: {e}")
    return presets


@app.post("/api/presets/normalization/{name}")
def save_normalization_preset(name: str, preset: NormalizationPreset):
    """Save or update a normalization preset."""
    # Prevent directory traversal
    clean_name = os.path.basename(name).replace("..", "")
    if not clean_name:
        raise HTTPException(status_code=400, detail="Nom de preset invalide")
    
    preset_path = os.path.join(NORMALIZATION_PRESETS_DIR, f"{clean_name}.json")
    try:
        with open(preset_path, "w", encoding="utf-8") as f:
            json.dump(preset.dict(), f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save preset: {e}")
    return {"message": f"Preset '{clean_name}' sauvegardé avec succès"}


@app.delete("/api/presets/normalization/{name}")
def delete_normalization_preset(name: str):
    """Delete a normalization preset."""
    clean_name = os.path.basename(name).replace("..", "")
    preset_path = os.path.join(NORMALIZATION_PRESETS_DIR, f"{clean_name}.json")
    if not os.path.exists(preset_path):
        raise HTTPException(status_code=404, detail="Preset introuvable")
    try:
        os.remove(preset_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete preset: {e}")
    return {"message": f"Preset '{clean_name}' supprimé"}


@app.get("/api/voicebox/profiles")
def get_voicebox_profiles():
    """Proxy endpoint to retrieve voice profiles from the local Voicebox server."""
    voicebox_url = f"{VOICEBOX_URL}/profiles"
    try:
        req = urllib.request.urlopen(voicebox_url, timeout=2)
        profiles = json.loads(req.read().decode('utf-8'))
        return profiles
    except Exception as e:
        # Return a fallback list for offline mode
        print(f"Warning: Failed to connect to Voicebox: {e}")
        return [
            {"id": "Narrator", "name": "Narrator", "language": "fr", "voice_type": "cloned"},
            {"id": "Pierre Arditi", "name": "Pierre Arditi", "language": "fr", "voice_type": "cloned"},
            {"id": "Adriana Karambeu", "name": "Adriana Karambeu", "language": "fr", "voice_type": "cloned"}
        ]
def _vb_get_json(path: str):
    """GET JSON depuis l'API VoiceBox."""
    with urllib.request.urlopen(f"{VOICEBOX_URL}{path}", timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _vb_download(path: str, dest: str):
    """Télécharge un fichier binaire depuis VoiceBox (ex: audio d'échantillon)."""
    with urllib.request.urlopen(f"{VOICEBOX_URL}{path}", timeout=120) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)


def _qc_post_json(path: str, payload: dict):
    """POST JSON vers le service QC et renvoie la réponse JSON."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{QC_URL}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def _find_vb_voice_by_name(voice_name: str):
    """Retrouve une voix VoiceBox par son nom (ou id), tolérant casse/espaces."""
    if not voice_name:
        return None
    try:
        voices = _vb_get_json("/profiles")
    except Exception:
        return None
    target = _normalize_voice_name(voice_name)
    for v in voices:
        if v.get("id") == voice_name or _normalize_voice_name(v.get("name") or "") == target:
            return v
    return None


def ensure_voiceprint_for_name(voice_name: str, force: bool = False) -> Optional[str]:
    """Renvoie le voice_id de l'empreinte de `voice_name`. Si l'empreinte n'existe
    pas encore (ou si `force=True`), la (re)FABRIQUE depuis VoiceBox (échantillon
    de référence → service QC), met à jour l'index, puis renvoie l'id.

    - `force=False` (défaut) : paresseux — ne fabrique que si absente. Une nouvelle
      voix VoiceBox est ainsi calibrée automatiquement à sa 1re génération.
    - `force=True` : refait l'empreinte à partir de l'échantillon VoiceBox ACTUEL
      (utile si la voix a été refaite dans VoiceBox). En cas d'échec, l'empreinte
      existante est conservée (jamais de perte)."""
    existing = resolve_voice_id(voice_name)
    if existing and not force:
        return existing
    if not QC_URL:
        return existing
    v = _find_vb_voice_by_name(voice_name)
    if not v or not v.get("id"):
        return existing
    vid = v["id"]
    vname = v.get("name") or vid
    try:
        samples = _vb_get_json(f"/profiles/{vid}/samples")
    except Exception:
        samples = []
    if not samples:
        return existing
    sid = samples[0].get("id")
    os.makedirs(VOICEPRINTS_DIR, exist_ok=True)
    tmp_dir = os.path.join(PROJECTS_DIR, ".voiceprints_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_wav = os.path.join(tmp_dir, f"{vid}.wav")
    try:
        _vb_download(f"/samples/{sid}", tmp_wav)
        res = _qc_post_json("/voiceprint_save", {"wav_path": tmp_wav, "voice_id": vid})
    except Exception as e:
        print(f"[WARN] Empreinte auto échouée pour '{vname}': {e}")
        return existing
    finally:
        if os.path.exists(tmp_wav):
            try:
                os.remove(tmp_wav)
            except Exception:
                pass
    try:
        meta = {"voice_id": vid, "name": vname, "sample_id": sid,
                "duration_sec": res.get("duration"), "dim": res.get("dim"),
                "updated_at": time.time()}
        with open(os.path.join(VOICEPRINTS_DIR, f"{vid}.meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        index_path = os.path.join(VOICEPRINTS_DIR, "index.json")
        index = {}
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index = json.load(f)
            except Exception:
                index = {}
        index[vname] = vid
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        ensure_default_voice_thresholds()
    except Exception as e:
        print(f"[WARN] Écriture index empreinte '{vname}': {e}")
    return vid


@app.post("/api/qc/voiceprints/sync")
def sync_voiceprints():
    """Construit/rafraîchit la bibliothèque d'empreintes à partir des voix VoiceBox.

    Pour chaque voix : récupère son échantillon de référence, le télécharge, et
    demande au service QC d'en calculer/ranger l'empreinte. Plus besoin de
    désigner manuellement une référence : chaque voix a la sienne.
    """
    if not QC_URL:
        raise HTTPException(status_code=503, detail="Service QC indisponible (QC_URL non défini).")

    tmp_dir = os.path.join(PROJECTS_DIR, ".voiceprints_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(VOICEPRINTS_DIR, exist_ok=True)

    try:
        voices = _vb_get_json("/profiles")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Impossible de lister les voix VoiceBox : {e}")

    index, synced, skipped = {}, [], []
    for v in voices:
        vid = v.get("id")
        vname = v.get("name") or vid
        if not vid:
            continue
        try:
            samples = _vb_get_json(f"/profiles/{vid}/samples")
        except Exception:
            samples = []
        if not samples:
            skipped.append(vname)
            continue

        sample = samples[0]  # échantillon de référence
        sid = sample.get("id")
        ref_text = sample.get("reference_text") or ""
        tmp_wav = os.path.join(tmp_dir, f"{vid}.wav")
        try:
            _vb_download(f"/samples/{sid}", tmp_wav)
            res = _qc_post_json("/voiceprint_save", {"wav_path": tmp_wav, "voice_id": vid})
        except Exception as e:
            skipped.append(f"{vname} (erreur: {e})")
            continue
        finally:
            if os.path.exists(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except Exception:
                    pass

        meta = {
            "voice_id": vid,
            "name": vname,
            "sample_id": sid,
            "duration_sec": res.get("duration"),
            "ref_text_len": len(ref_text),
            "dim": res.get("dim"),
            "updated_at": time.time(),
        }
        with open(os.path.join(VOICEPRINTS_DIR, f"{vid}.meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        index[vname] = vid
        synced.append(vname)

    with open(os.path.join(VOICEPRINTS_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    # Toute nouvelle voix sans réglage reçoit la série par défaut.
    ensure_default_voice_thresholds()

    return {
        "synced": len(synced),
        "skipped": len(skipped),
        "voices_synced": sorted(synced),
        "voices_skipped": skipped,
    }


@app.get("/api/qc/voiceprints")
def list_voiceprints():
    """Liste les empreintes de voix disponibles (bibliothèque QC)."""
    result = []
    if os.path.isdir(VOICEPRINTS_DIR):
        for f in os.listdir(VOICEPRINTS_DIR):
            if f.endswith(".meta.json"):
                try:
                    with open(os.path.join(VOICEPRINTS_DIR, f), "r", encoding="utf-8") as fh:
                        result.append(json.load(fh))
                except Exception:
                    pass
    return {"count": len(result), "voiceprints": sorted(result, key=lambda x: x.get("name", ""))}


@app.get("/api/tts_speed")
def get_tts_speed():
    """Renvoie la vitesse TTS mesurée (ms/caractère) pour le moteur/modèle courant.

    Sert au frontend à estimer dynamiquement l'avancement de la génération,
    au lieu d'une constante figée. Se cale tout seul sur le matériel/backend.
    """
    data = load_tts_speed()
    eng, msz = get_tts_engine_model()
    key = f"{eng}|{msz}"
    entry = data.get(key)
    if not entry and data:
        # Repli : l'entrée la plus récemment mise à jour.
        entry = sorted(data.values(), key=lambda e: e.get("updated_at", 0))[-1]
    if not entry:
        entry = {"ms_per_char": TTS_SPEED_DEFAULT_RATE_MS, "base_ms": TTS_SPEED_DEFAULT_BASE_MS, "samples": 0}
    return {
        "ms_per_char": entry.get("ms_per_char", TTS_SPEED_DEFAULT_RATE_MS),
        "base_ms": entry.get("base_ms", TTS_SPEED_DEFAULT_BASE_MS),
        "samples": entry.get("samples", 0),
        "engine": eng,
        "model_size": msz,
        "by_model": data,
    }


@app.post("/api/projects/{name}/queue")
async def enqueue_segments(name: str, req: QueueRequest):
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(project_dir) or not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")

    # Resolve project type
    meta_path = os.path.join(project_dir, "meta.json")
    project_type = "novel"
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
            project_type = meta_data.get("type", "novel")
        except Exception:
            pass

    if not req.ranges or req.ranges.lower() == "all":
        segment_nums = resolve_all_segments(project_dir)
    else:
        segment_nums = parse_ranges(req.ranges)

    global_tts_queue.add_segments(name, segment_nums, req.voice, project_type,
                                   req.versions or 1, req.qc_threshold, req.max_attempts or 20,
                                   bool(req.qc_batch))

    pq = get_project_queue(name)
    pending_segments = [
        {"num": item["num"], "voice": item["voice"], "versions": item["versions"], "global_position": idx + 1}
        for idx, item in enumerate(global_tts_queue.pending_tasks)
        if item["project_name"] == name
    ]

    return {
        "message": f"{len(segment_nums)} segments ajoutés à la file d'attente",
        "active_segment": pq.active_segment,
        "pending_segments": pending_segments
    }


@app.post("/api/projects/{name}/queue/stop")
async def stop_queue(name: str):
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(project_dir) or not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")

    await global_tts_queue.stop()
    
    pq = get_project_queue(name)
    pq.active_segment = None
    
    if name in segment_cache:
        segment_cache[name] = {}
        
    return {"message": "La file d'attente a été stoppée et purgée."}


@app.get("/api/projects/{name}/terminal")
async def run_tool_sse(
    name: str,
    action: str = Query(...),
    ranges: Optional[str] = Query(None),
    voice: Optional[str] = Query(None),
    play_type: Optional[str] = Query(None),
    output_name: Optional[str] = Query(None),
    versions: Optional[int] = Query(1),
    qc_threshold: Optional[float] = Query(None),
    max_attempts: Optional[int] = Query(20),
    qc_batch: Optional[bool] = Query(False),
):
    """
    Executes script in a subprocess and streams output logs live via SSE.
    """
    cmd = []
    
    if action == "extract":
        book_dir = os.path.join(PROJECTS_DIR, name, "book")
        pdf_file = None
        if os.path.exists(book_dir):
            for f in os.listdir(book_dir):
                if f.lower().endswith(".pdf"):
                    pdf_file = f
                    break

        # Type de projet : détermine le convertisseur PDF (théâtre = italique→
        # parenthèses ; roman = texte brut, images ignorées).
        proj_type = "novel"
        meta_p = os.path.join(PROJECTS_DIR, name, "meta.json")
        if os.path.exists(meta_p):
            try:
                with open(meta_p, "r", encoding="utf-8") as f:
                    proj_type = json.load(f).get("type", "novel")
            except Exception:
                pass

        if pdf_file:
            pdf_path = os.path.join(book_dir, pdf_file)
            if proj_type == "theatre":
                script_path = os.path.join(TOOLS_DIR, "convertpdftoabs_theatre.py")
            else:
                script_path = os.path.join(TOOLS_DIR, "convertpdftotxt_novel.py")
            cmd = [PYTHON_EXE, script_path, pdf_path]
        else:
            # Extract epub to txt
            script_path = os.path.join(TOOLS_DIR, "epub2txt4audioBook.py")
            cmd = [PYTHON_EXE, script_path, "--project", name]
            
    elif action == "parse_theatre":
        # parse_theatre.py
        book_dir = os.path.join(PROJECTS_DIR, name, "book")
        # Find raw source txt: prefer _formated.txt if it exists
        src_file = None
        if os.path.exists(book_dir):
            for f in os.listdir(book_dir):
                if f.lower().endswith("_formated.txt"):
                    src_file = f
                    break
        if not src_file and os.path.exists(book_dir):
            for f in os.listdir(book_dir):
                if f.lower().endswith(".txt") and not f.lower().startswith("seg"):
                    if not f.lower().endswith("_parsed.txt") and not f.lower().endswith("_chunked.txt"):
                        src_file = f
                        break
        
        if not src_file:
            async def error_generator():
                yield "data: [ERREUR] Aucun fichier texte source trouvé à parser.\n\n"
            return StreamingResponse(error_generator(), media_type="text/event-stream")
        
        src_path = os.path.join(book_dir, src_file)
        if src_file.endswith("_formated.txt"):
            dest_file = src_file.replace("_formated.txt", "_parsed.txt")
        else:
            dest_file = src_file.replace(".txt", "_parsed.txt")
        dest_path = os.path.join(book_dir, dest_file)
        
        script_path = os.path.join(TOOLS_DIR, "parse_theatre.py")
        cmd = [PYTHON_EXE, script_path, src_path, dest_path]
        
    elif action == "chunk_theatre":
        # chunker_theatre.py
        book_dir = os.path.join(PROJECTS_DIR, name, "book")
        # Find parsed txt
        parsed_file = None
        if os.path.exists(book_dir):
            for f in os.listdir(book_dir):
                if f.lower().endswith("_parsed.txt"):
                    parsed_file = f
                    break
        
        if not parsed_file:
            async def error_generator():
                yield "data: [ERREUR] Aucun fichier de théâtre parsé (*_parsed.txt) trouvé.\n\n"
            return StreamingResponse(error_generator(), media_type="text/event-stream")
        
        src_path = os.path.join(book_dir, parsed_file)
        dest_path = os.path.join(book_dir, parsed_file.replace("_parsed.txt", "_chunked.txt"))
        
        script_path = os.path.join(TOOLS_DIR, "chunker_theatre.py")
        cmd = [PYTHON_EXE, script_path, src_path, dest_path]
        
    elif action == "chunk_book":
        # chunker_book.py
        book_dir = os.path.join(PROJECTS_DIR, name, "book")
        # Find raw source txt (not chunked or parsed)
        src_file = None
        if os.path.exists(book_dir):
            for f in os.listdir(book_dir):
                if f.lower().endswith(".txt") and not f.lower().startswith("seg"):
                    if not f.lower().endswith("_chunked.txt") and not f.lower().endswith("_parsed.txt"):
                        src_file = f
                        break
        
        if not src_file:
            async def error_generator():
                yield "data: [ERREUR] Aucun fichier texte source brut trouvé dans /book.\n\n"
            return StreamingResponse(error_generator(), media_type="text/event-stream")
        
        src_path = os.path.join(book_dir, src_file)
        dest_path = os.path.join(book_dir, src_file.replace(".txt", "_chunked.txt"))
        
        script_path = os.path.join(TOOLS_DIR, "chunker_book.py")
        cmd = [PYTHON_EXE, script_path, src_path, dest_path]
        
    elif action == "split_theatre":
        # Force le splitter théâtre (utilisé aussi par les romans à personnages
        # une fois les rôles distribués + assemblés en _parsed.txt puis _chunked.txt).
        script_path = os.path.join(TOOLS_DIR, "split_theatre.py")
        cmd = [PYTHON_EXE, script_path, name]

    elif action == "split":
        # Determine project type from meta.json or dynamic fallback
        project_dir = os.path.join(PROJECTS_DIR, name)
        meta_path = os.path.join(project_dir, "meta.json")
        project_type = "novel"
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                project_type = meta_data.get("type", "novel")
            except Exception:
                pass
        else:
            book_dir = os.path.join(project_dir, "book")
            has_parsed_file = False
            if os.path.exists(book_dir):
                has_parsed_file = any(f.lower().endswith("_parsed.txt") for f in os.listdir(book_dir))
            project_type = "theatre" if has_parsed_file else "novel"

        if project_type == "theatre":
            script_path = os.path.join(TOOLS_DIR, "split_theatre.py")
            cmd = [PYTHON_EXE, script_path, name]
        else:
            script_path = os.path.join(TOOLS_DIR, "split_book.py")
            cmd = [PYTHON_EXE, script_path, name]

    elif action == "generate":
        project_dir = os.path.join(PROJECTS_DIR, name)
        meta_path = os.path.join(project_dir, "meta.json")
        project_type = "novel"
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                project_type = meta_data.get("type", "novel")
            except Exception:
                pass
        else:
            book_dir = os.path.join(project_dir, "book")
            has_parsed_file = False
            if os.path.exists(book_dir):
                has_parsed_file = any(f.lower().endswith("_parsed.txt") for f in os.listdir(book_dir))
            project_type = "theatre" if has_parsed_file else "novel"

        if not ranges or ranges.lower() == "all":
            segment_nums = resolve_all_segments(project_dir)
        else:
            segment_nums = parse_ranges(ranges)

        global_tts_queue.add_segments(name, segment_nums, voice, project_type,
                                       versions or 1, qc_threshold, max_attempts or 20,
                                       bool(qc_batch))
        pq = get_project_queue(name)

        async def generate_and_finish():
            """Stream logs and close SSE as soon as the global queue goes idle."""
            # Edge case: queue already finished before we started listening
            if global_tts_queue.active_task is None and len(global_tts_queue.pending_tasks) == 0:
                yield "data: [INFO] Génération déjà terminée.\n\n"
                yield "data: [INFO] Commande terminée avec le code de sortie : 0\n\n"
                return
            async for chunk in pq.listen():
                yield chunk
                # Check after every log line if the queue has become empty
                if global_tts_queue.active_task is None and len(global_tts_queue.pending_tasks) == 0:
                    yield "data: [INFO] Commande terminée avec le code de sortie : 0\n\n"
                    return

        return StreamingResponse(generate_and_finish(), media_type="text/event-stream")

    elif action == "listen":
        pq = get_project_queue(name)
        return StreamingResponse(pq.listen(), media_type="text/event-stream")
            
    elif action == "concat":
        # concat_2mp3_audio
        script_path = os.path.join(TOOLS_DIR, "concat_2mp3_audio.py")
        cmd = [PYTHON_EXE, script_path, name]
        if ranges or output_name:
            cmd.append(ranges or "all")
        if output_name:
            out_file = output_name.strip()
            if out_file and not out_file.lower().endswith(".mp3"):
                out_file += ".mp3"
            cmd.append(out_file)
            
    else:
        raise HTTPException(status_code=400, detail="Action inconnue")

    async def log_generator():
        yield f"data: [INFO] Démarrage de la commande : {' '.join(cmd)}\n\n"
        try:
            # Run process async with unbuffered stdout and the root directory as cwd
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=BASE_DIR,
                env=env
            )
            
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                decoded_line = line.decode('utf-8', errors='ignore').rstrip('\r\n')
                # Yield lines to front in proper SSE format
                yield f"data: {decoded_line}\n\n"
                
            await process.wait()
            yield f"data: [INFO] Commande terminée avec le code de sortie : {process.returncode}\n\n"
        except Exception as e:
            yield f"data: [ERREUR] Impossible de lancer le script: {str(e)}\n\n"

    return StreamingResponse(log_generator(), media_type="text/event-stream")


# Migration unique des seuils QC par projet → store central par voix, puis
# attribution de la série par défaut à toute voix encore sans réglage.
try:
    migrate_qc_thresholds_to_central()
    ensure_default_voice_thresholds()
except Exception as _e:
    print(f"[WARN] Init seuils QC: {_e}")


if __name__ == "__main__":
    import uvicorn
    # Run on port 17490
    uvicorn.run(app, host="0.0.0.0", port=17490)
