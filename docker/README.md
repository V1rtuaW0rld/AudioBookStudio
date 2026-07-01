# AudioBookStudio — Déploiement Docker

Stack à 4 services orchestrée par `docker-compose.yml` (à la racine).

| Service | Rôle | Port | Exposition |
|---|---|---|---|
| `frontend` | UI AudioBookStudio (nginx, proxy `/api`, `/audio`) | 5180 | hôte |
| `orchestrator` | API FastAPI + pipeline (tools) | 17490 | hôte |
| `voicebox` | Moteur TTS + **UI VoiceBox (création des voix)** | 17493 | hôte |
| `qc` | Contrôle qualité vocal (ONNX, isolé Python 3.10) | 8001 | interne |

Deux interfaces :
- **http://localhost:5180** — AudioBookStudio (gestion des livres/pièces, génération)
- **http://localhost:17493** — VoiceBox (création/clonage des voix)

## Lancement

Le **GPU NVIDIA est activé par défaut**. Sur une machine avec GPU (Windows ou
Linux + `nvidia-container-toolkit`), il suffit de :

```bash
docker compose up -d
```

Sur une machine **sans GPU** (CPU uniquement) :

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d
```

Lanceurs de confort (auto-détection GPU/CPU), optionnels :

```powershell
.\run-docker.ps1            # Windows
```
```bash
./run-docker.sh            # Linux / macOS
```

Interfaces : AudioBookStudio http://localhost:5180 — VoiceBox http://localhost:17493 — API http://localhost:17490

### Multi-plateforme

La stack est portable Windows **et** Linux : volumes en chemins relatifs
(`./Projects`, `./VoiceBox/data`…), images `linux/amd64`, et la réservation GPU
utilise le mécanisme standard `nvidia-container-toolkit` (identique sous Linux).

## GPU

L'image `voicebox` embarque **torch CUDA** (pas besoin de télécharger un backend
comme le fait le MSI). Le GPU est réservé **par défaut** dans `docker-compose.yml`
(`deploy.resources.reservations.devices`). L'override [docker-compose.cpu.yml](../docker-compose.cpu.yml)
le retire (via le tag `!override`) pour les machines sans GPU.

Pré-requis hôte : driver NVIDIA + Docker Desktop/WSL2 avec support GPU. Test :

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## Volumes / données

Tous les volumes sont des **bind-mounts relatifs** au dépôt (portables, pas de
chemin spécifique à une machine) :

- `./Projects` → orchestrator + qc (projets, audio, segments) — **chemin identique** `/app/Projects` dans les deux
- `./Presets`, `./config` → orchestrator
- `./VoiceBox/data` → voicebox : DB `voicebox.db` + `profiles/` (voix) + `models/` (modèles TTS)
- `./tools/QC` → qc (modèle `voxceleb.onnx` + scripts)

Les modèles TTS se téléchargent au **premier usage** dans `./VoiceBox/data/models`
(via `VOICEBOX_MODELS_DIR=/app/data/models`). Rien n'est codé en dur vers le
cache personnel de l'utilisateur ; une install from-scratch télécharge ses
propres modèles.

> Si le VoiceBox **natif** (`just dev-web`) et le conteneur tournent en même
> temps sur le même `./VoiceBox/data`, risque de verrou SQLite. Lancer l'un OU
> l'autre.

### Réutiliser une `voicebox.db` créée sous Windows

Une DB VoiceBox créée par l'app **native Windows** stocke les chemins des
échantillons (`profile_samples.audio_path`) avec des antislashs `\`, illisibles
sous Linux (erreur `FileNotFoundError` à la génération). Migration une fois :

```bash
docker compose stop voicebox
docker run --rm -v "$PWD/VoiceBox/data:/data" python:3.11-slim python -c "import sqlite3;c=sqlite3.connect('/data/voicebox.db');cur=c.cursor();[cur.execute('UPDATE profile_samples SET audio_path=? WHERE rowid=?',(p.replace(chr(92),'/'),r)) for r,p in cur.execute('SELECT rowid,audio_path FROM profile_samples').fetchall() if p and chr(92) in p];c.commit();c.close()"
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d voicebox
```

Une install **from-scratch** (voix créées dans le conteneur) n'est pas concernée.

## Variables d'environnement clés (orchestrator)

- `VOICEBOX_URL=http://voicebox:17493`
- `QC_URL=http://qc:8001`

Sans ces variables (lancement Windows natif), le code retombe sur le comportement
local d'origine (subprocess QC, `127.0.0.1:17493`).

## Note build — version de pip (image voicebox)

Fenêtre de compatibilité étroite sur cet hôte, gérée dans `VoiceBox/Dockerfile.web` :
pip **25.x** uniquement (24.x casse le parsing `--find-links`, 26.x segfault).
Les gros paquets ML sont pré-installés avant `requirements.txt` pour éviter un
SIGSEGV intermittent du résolveur, et Qwen3-TTS est installé en `--no-deps`.
