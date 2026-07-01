# ============================================================
#  AudioBookStudio — Lanceur Docker (Windows)
#
#  Le GPU NVIDIA est activé PAR DÉFAUT. Usage simple :
#    docker compose up -d        # ou : .\run-docker.ps1
#
#  Ce script n'est qu'un confort (auto-bascule CPU si pas de GPU).
#    .\run-docker.ps1            # auto : GPU si présent, sinon CPU
#    .\run-docker.ps1 -Force cpu # force le mode CPU
#    .\run-docker.ps1 -Down      # arrête la stack
# ============================================================
param(
    [ValidateSet("auto", "cpu", "gpu")]
    [string]$Force = "auto",
    [switch]$Down
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Test-NvidiaGpu {
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { return $false }
    try { & nvidia-smi -L *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}

if ($Down) {
    docker compose down
    exit 0
}

$mode = $Force
if ($mode -eq "auto") { $mode = if (Test-NvidiaGpu) { "gpu" } else { "cpu" } }

if ($mode -eq "cpu") {
    Write-Host "Mode CPU." -ForegroundColor Cyan
    docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d
} else {
    Write-Host "Mode GPU (defaut)." -ForegroundColor Green
    docker compose up -d
}

Write-Host "`n  → AudioBookStudio : http://localhost:5180"
Write-Host "  → VoiceBox (voix) : http://localhost:17493"
