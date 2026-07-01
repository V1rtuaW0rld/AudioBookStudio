#!/usr/bin/env bash
# ============================================================
#  AudioBookStudio — Lanceur Docker (Linux/macOS)
#
#  Le GPU NVIDIA est activé PAR DÉFAUT. Usage simple :
#    docker compose up -d        # ou : ./run-docker.sh
#
#  Ce script n'est qu'un confort (auto-bascule CPU si pas de GPU).
#    ./run-docker.sh             # auto : GPU si présent, sinon CPU
#    ./run-docker.sh cpu         # force le mode CPU
#    ./run-docker.sh down        # arrête la stack
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

if [ "${1:-}" = "down" ]; then
    docker compose down
    exit 0
fi

mode="${1:-auto}"
if [ "$mode" = "auto" ]; then
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
        mode="gpu"
    else
        mode="cpu"
    fi
fi

if [ "$mode" = "cpu" ]; then
    echo "Mode CPU."
    docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d
else
    echo "Mode GPU (défaut)."
    docker compose up -d
fi

echo ""
echo "  → AudioBookStudio : http://localhost:5180"
echo "  → VoiceBox (voix) : http://localhost:17493"
