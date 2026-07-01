import os
import sys
import json
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


async def qc_compute_score(ref_npy: str, wav_path: str):
    """Calcule le score de similarité vocale.

    Retourne (score|None, log_text).
    - Si QC_URL est défini : appel HTTP du microservice QC (Docker).
    - Sinon : appel subprocess du venv QC local (comportement Windows).
    """
    if QC_URL:
        loop = asyncio.get_event_loop()

        def _do():
            data = json.dumps({"ref_npy": ref_npy, "wav_path": wav_path}).encode("utf-8")
            req = urllib.request.Request(
                f"{QC_URL}/verify",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            result = await loop.run_in_executor(None, _do)
            return result.get("score"), ""
        except urllib.error.HTTPError as e:
            return None, e.read().decode("utf-8", errors="ignore")
        except Exception as e:
            return None, str(e)

    # --- Fallback subprocess (venv QC local) ---
    qc_tool_dir = os.path.join(TOOLS_DIR, "QC")
    qc_python = os.path.join(qc_tool_dir, "venv", "Scripts", "python.exe")
    qc_script = os.path.join(qc_tool_dir, "verify_voice.py")

    if not os.path.exists(qc_python) or not os.path.exists(qc_script):
        return None, "Outil de vérification vocale (QC) non configuré ou venv manquant."

    cmd = [qc_python, qc_script, ref_npy, wav_path]
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=qc_tool_dir, env=env
        )
        stdout_data, _ = await process.communicate()
        output_text = stdout_data.decode("utf-8", errors="ignore")
        score = None
        for line in output_text.splitlines():
            if "Score de similarite :" in line:
                try:
                    score = float(line.split("Score de similarite :")[-1].strip())
                except ValueError:
                    pass
        return score, output_text
    except Exception as e:
        return None, str(e)


async def qc_create_voiceprint(wav_path: str, dest_npy: str):
    """Crée une empreinte de référence (.npy).

    Retourne (ok: bool, log_text).
    - Si QC_URL est défini : appel HTTP du microservice QC (Docker).
    - Sinon : appel subprocess du venv QC local (comportement Windows).
    """
    if QC_URL:
        loop = asyncio.get_event_loop()

        def _do():
            data = json.dumps({"wav_path": wav_path, "output_npy": dest_npy}).encode("utf-8")
            req = urllib.request.Request(
                f"{QC_URL}/voiceprint",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            result = await loop.run_in_executor(None, _do)
            return os.path.exists(dest_npy), result.get("message", "")
        except urllib.error.HTTPError as e:
            return False, e.read().decode("utf-8", errors="ignore")
        except Exception as e:
            return False, str(e)

    # --- Fallback subprocess (venv QC local) ---
    qc_tool_dir = os.path.join(TOOLS_DIR, "QC")
    qc_python = os.path.join(qc_tool_dir, "venv", "Scripts", "python.exe")
    qc_script = os.path.join(qc_tool_dir, "create_voiceprint.py")

    if not os.path.exists(qc_python):
        return False, "Environnement virtuel de QC introuvable (venv/Scripts/python.exe dans tools/QC/)"
    if not os.path.exists(qc_script):
        return False, "Script create_voiceprint.py introuvable dans tools/QC/"

    cmd = [qc_python, qc_script, wav_path, dest_npy]
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=qc_tool_dir, env=env
        )
        stdout_data, _ = await process.communicate()
        output_text = stdout_data.decode("utf-8", errors="ignore")
        if process.returncode != 0:
            return False, f"Le script s'est terminé avec le code {process.returncode}. Log:\n{output_text}"
        return os.path.exists(dest_npy), output_text
    except Exception as e:
        return False, str(e)


async def run_auto_evaluation(project_name: str, num: int, pq: Optional[ProjectQueue] = None):
    """Computes similarity score for a newly generated segment WAV and saves it to JSON."""
    project_dir = os.path.join(PROJECTS_DIR, project_name)
    tts_dir = os.path.join(project_dir, "tts")
    json_name = f"seg{str(num).zfill(5)}.json"
    json_path = os.path.join(tts_dir, json_name)

    if not os.path.exists(json_path):
        return

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

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            seg_data = json.load(f)
        actor = "Narrator" if project_type in ["novel", "novel_multi"] else seg_data.get("profile_id", "Narrator")
    except Exception:
        return

    # Check if reference exists
    qc_dir = os.path.join(project_dir, "QC")
    ref_npy = os.path.join(qc_dir, f"ref_{actor.strip()}.npy")
    if not os.path.exists(ref_npy):
        return

    wav_name = f"seg{str(num).zfill(5)}.wav"
    wav_path = os.path.join(project_dir, "audio", wav_name)
    if not os.path.exists(wav_path):
        return

    try:
        score, log_text = await qc_compute_score(ref_npy, wav_path)
        if score is not None:
            seg_data["qc_score"] = score
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(seg_data, f, ensure_ascii=False, indent=2)
            if pq:
                pq.emit_log(f"[INFO] Evaluation QC automatique : similarite de {score*100:.0f}% detectee.")
    except Exception as e:
        if pq:
            pq.emit_log(f"[WARN] Echec de l'evaluation QC automatique : {str(e)}")


class GlobalTTSQueue:
    def __init__(self):
        self.pending_tasks: List[Dict] = []  # List of {"project_name": str, "num": int, "voice": str, "versions": int, "project_type": str}
        self.active_task: Optional[Dict] = None
        self.worker_task: Optional[asyncio.Task] = None
        self.active_process = None
        self.aborted = False

    def add_segments(self, project_name: str, segment_nums: List[int], voice: Optional[str], project_type: str, versions: int = 1):
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
                "project_type": project_type
            })
            added.append(num)

        if added:
            pq.emit_log(f"[INFO] File d'attente globale : ajout des segments {added} pour le projet '{project_name}' (voix: {voice or 'défaut'}, versions: {versions})")

        if not self.worker_task or self.worker_task.done():
            self.worker_task = asyncio.create_task(self.run_worker())

    async def run_worker(self):
        while self.pending_tasks:
            task = self.pending_tasks.pop(0)
            self.active_task = task
            
            project_name = task["project_name"]
            num = task["num"]
            voice = task["voice"]
            versions = task["versions"]
            project_type = task["project_type"]
            
            pq = get_project_queue(project_name)
            pq.active_segment = num
            
            project_dir = os.path.join(PROJECTS_DIR, project_name)
            audio_dir = os.path.join(project_dir, "audio")
            os.makedirs(audio_dir, exist_ok=True)

            # Clear existing qc_score from segment JSON since we are about to regenerate the audio
            tts_dir = os.path.join(project_dir, "tts")
            json_name = f"seg{str(num).zfill(5)}.json"
            json_path = os.path.join(tts_dir, json_name)
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        seg_data = json.load(f)
                    if "qc_score" in seg_data:
                        del seg_data["qc_score"]
                        with open(json_path, "w", encoding="utf-8") as f:
                            json.dump(seg_data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"Warning: Failed to clear qc_score before generation: {e}")

            # Nombre de caractères du segment (pour le calibrage de vitesse).
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

            runs = range(1, versions + 1) if versions > 1 else [0]

            for run_idx in runs:
                if project_type == "theatre":
                    script_path = os.path.join(TOOLS_DIR, "generate_audio_theatre.py")
                    cmd = [PYTHON_EXE, script_path, project_name, str(num)]
                    if voice:
                        cmd.append(voice)
                else:
                    script_path = os.path.join(TOOLS_DIR, "generate_audio.py")
                    cmd = [PYTHON_EXE, script_path, project_name, str(num), voice or "Narrator"]

                run_desc = f"version {run_idx}" if versions > 1 else "unique"
                pq.emit_log(f"[INFO] Traitement du segment {num} ({run_desc}) pour le projet '{project_name}' ...")
                pq.emit_log(f"[INFO] Commande : {' '.join(cmd)}")

                try:
                    env = os.environ.copy()
                    env["PYTHONUNBUFFERED"] = "1"
                    gen_start = time.monotonic()
                    self.active_process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        cwd=BASE_DIR,
                        env=env
                    )

                    while True:
                        line = await self.active_process.stdout.readline()
                        if not line:
                            break
                        decoded_line = line.decode('utf-8', errors='ignore').rstrip('\r\n')
                        pq.emit_log(decoded_line)

                    await self.active_process.wait()
                    if self.active_process.returncode == 0:
                        # Calibrage : durée réelle observée pour ce segment.
                        if seg_chars > 0:
                            duration_ms = (time.monotonic() - gen_start) * 1000.0
                            eng, msz = get_tts_engine_model()
                            update_tts_speed(eng, msz, seg_chars, duration_ms)
                        if versions > 1:
                            main_wav_name = f"seg{str(num).zfill(5)}.wav"
                            version_wav_name = f"seg{str(num).zfill(5)}_v{run_idx}.wav"
                            main_wav_path = os.path.join(audio_dir, main_wav_name)
                            version_wav_path = os.path.join(audio_dir, version_wav_name)
                            if os.path.exists(main_wav_path):
                                if os.path.exists(version_wav_path):
                                    os.remove(version_wav_path)
                                os.rename(main_wav_path, version_wav_path)
                                pq.emit_log(f"[INFO] Fichier renommé en {version_wav_name}")
                            pq.emit_log(f"[INFO] Segment {num} ({run_desc}) généré avec succès.")
                        else:
                            await run_auto_evaluation(project_name, num, pq)
                            pq.emit_log(f"[INFO] Segment {num} ({run_desc}) généré avec succès.")
                    else:
                        if self.aborted:
                            pq.emit_log(f"[INFO] Génération annulée par l'utilisateur.")
                        else:
                            pq.emit_log(f"[ERREUR] Le segment {num} ({run_desc}) a échoué (code de sortie: {self.active_process.returncode})")
                except Exception as e:
                    if self.aborted:
                        pq.emit_log(f"[INFO] Génération annulée par l'utilisateur.")
                    else:
                        pq.emit_log(f"[ERREUR] Impossible de lancer la génération pour le segment {num}: {str(e)}")
                finally:
                    self.active_process = None

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
        if ext not in [".epub"]:
            raise HTTPException(status_code=400, detail="Seuls les fichiers .epub sont acceptés")
        
    dest_path = os.path.join(book_dir, filename)
    try:
        content = await file.read()
        with open(dest_path, "wb") as f:
            f.write(content)
        return {"filename": filename, "message": f"Fichier {ext.upper()[1:]} téléversé avec succès"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
            if project_type == "theatre":
                characters.add(profile_id)
                
            wav_name = filename.replace(".json", ".wav")
            has_wav = idx in existing_wavs
            wav_mtime = wav_mtimes.get(idx) if has_wav else None
            
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
                "qc_score": qc_score
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
                "qc_score": None
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
    # Scan for QC reference files
    qc_dir = os.path.join(path, "QC")
    qc_references = []
    if os.path.exists(qc_dir):
        for f in os.listdir(qc_dir):
            if f.startswith("ref_") and f.endswith(".npy"):
                qc_references.append(f[4:-4])

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
        "voice_mapping": voice_mapping,
        "mp3_files": mp3_files,
        "active_segment": active_seg,
        "pending_segments": pending_segments,
        "global_queue_active": global_queue_active,
        "qc_references": qc_references,
        "normalization_rules": meta_data.get("normalization_rules", []),
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

    if name in segment_cache:
        segment_cache[name] = {}

    return {
        "message": f"Version {data.version} validée pour le segment {num}. {deleted_count} versions alternatives supprimées.",
        "audio_url": f"/audio/{name}/audio/{prefix}.wav"
    }






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


@app.post("/api/projects/{name}/voiceprint")
async def create_voiceprint(name: str, req: VoiceprintRequest):
    """Creates a voiceprint reference file for an actor using the isolated QC venv."""
    project_dir = os.path.join(PROJECTS_DIR, name)
    if not os.path.exists(project_dir) or not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable")

    wav_name = f"seg{str(req.segment_num).zfill(5)}.wav"
    wav_path = os.path.join(project_dir, "audio", wav_name)

    if not os.path.exists(wav_path):
        raise HTTPException(status_code=400, detail=f"Le fichier audio {wav_name} n'existe pas. Veuillez le générer d'abord.")

    qc_dir = os.path.join(project_dir, "QC")
    os.makedirs(qc_dir, exist_ok=True)

    dest_npy = os.path.join(qc_dir, f"ref_{req.actor.strip()}.npy")

    try:
        ok, output_text = await qc_create_voiceprint(wav_path, dest_npy)
        if not ok:
            raise Exception(output_text or "Le fichier d'empreinte n'a pas été créé.")
        return {"message": f"Empreinte de référence créée avec succès pour {req.actor} !", "log": output_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la génération de l'empreinte : {str(e)}")


@app.post("/api/projects/{name}/segments/{num}/qc_score")
async def get_segment_qc_score(name: str, num: int):
    """Computes the similarity score for a segment against its actor's reference voiceprint."""
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

    # 1. Find actor name from segment JSON
    tts_dir = os.path.join(project_dir, "tts")
    json_name = f"seg{str(num).zfill(5)}.json"
    json_path = os.path.join(tts_dir, json_name)
    
    actor = "Narrator"
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                seg_data = json.load(f)
            actor = "Narrator" if project_type in ["novel", "novel_multi"] else seg_data.get("profile_id", "Narrator")
        except Exception:
            pass

    # 2. Check reference voiceprint
    qc_dir = os.path.join(project_dir, "QC")
    ref_npy = os.path.join(qc_dir, f"ref_{actor.strip()}.npy")
    if not os.path.exists(ref_npy):
        raise HTTPException(status_code=400, detail=f"Empreinte de référence manquante pour l'acteur '{actor}'")

    # 3. Check target WAV file
    wav_name = f"seg{str(num).zfill(5)}.wav"
    wav_path = os.path.join(project_dir, "audio", wav_name)
    if not os.path.exists(wav_path):
        raise HTTPException(status_code=400, detail="Fichier audio du segment introuvable. Veuillez d'abord le générer.")

    try:
        score, output_text = await qc_compute_score(ref_npy, wav_path)

        if score is None:
            raise Exception(f"Impossible de lire le score dans la sortie du script. Log:\n{output_text}")

        # Save score back to segment JSON
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    seg_data = json.load(f)
                seg_data["qc_score"] = score
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(seg_data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[WARN] Failed to write qc_score to JSON: {e}")

        return {"score": score, "actor": actor}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de calcul du score QC : {str(e)}")


@app.post("/api/projects/{name}/actors/{actor}/verify_batch")
async def verify_batch_qc(name: str, actor: str):
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

    qc_dir = os.path.join(project_dir, "QC")
    ref_npy = os.path.join(qc_dir, f"ref_{actor.strip()}.npy")
    if not os.path.exists(ref_npy):
        raise HTTPException(status_code=400, detail=f"Empreinte de référence manquante pour l'acteur '{actor}'")

    tts_dir = os.path.join(project_dir, "tts")
    audio_dir = os.path.join(project_dir, "audio")

    if not os.path.exists(tts_dir):
        return {"processed": 0, "message": "Aucun segment trouvé."}

    # Find all segments belonging to the actor that have a WAV but no qc_score
    eligible_segments = []
    for f in os.listdir(tts_dir):
        if f.startswith("seg") and f.endswith(".json"):
            try:
                num = int(f[3:8])
                json_path = os.path.join(tts_dir, f)
                with open(json_path, "r", encoding="utf-8") as file:
                    seg_data = json.load(file)
                
                seg_actor = "Narrator" if project_type in ["novel", "novel_multi"] else seg_data.get("profile_id", "Narrator")
                if seg_actor.strip() == actor.strip():
                    wav_name = f.replace(".json", ".wav")
                    wav_path = os.path.join(audio_dir, wav_name)
                    if os.path.exists(wav_path) and seg_data.get("qc_score") is None:
                        eligible_segments.append(num)
            except Exception:
                continue

    eligible_segments.sort()
    count = 0
    for num in eligible_segments:
        try:
            await run_auto_evaluation(name, num)
            count += 1
        except Exception as e:
            print(f"[WARN] Batch QC failed for segment {num}: {e}")

    if count > 0 and name in segment_cache:
        segment_cache[name] = {}

    return {
        "processed": count,
        "message": f"Calcul par lot terminé : {count} segments évalués pour l'acteur '{actor}'."
    }



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

    global_tts_queue.add_segments(name, segment_nums, req.voice, project_type, req.versions or 1)
    
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
    versions: Optional[int] = Query(1)
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
        if pdf_file:
            script_path = os.path.join(TOOLS_DIR, "convertpdftoabs_theatre.py")
            pdf_path = os.path.join(book_dir, pdf_file)
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

        global_tts_queue.add_segments(name, segment_nums, voice, project_type, versions or 1)
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


if __name__ == "__main__":
    import uvicorn
    # Run on port 17490
    uvicorn.run(app, host="0.0.0.0", port=17490)
