# ==========================================
#  AudioBookStudio Orchestrator Launcher
# ==========================================

Write-Host "=== AudioBookStudio Orchestrator ===" -ForegroundColor Magenta

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$server = Join-Path $root "webapp\backend\server.py"
$frontend = Join-Path $root "webapp\frontend"

Write-Host "Démarrage du Backend (port 17490)..." -ForegroundColor Yellow
$backendJob = Start-Process -PassThru -NoNewWindow `
    -FilePath $python `
    -ArgumentList $server

Start-Sleep -Seconds 2

Write-Host "Démarrage du Frontend (port 5180)..." -ForegroundColor Yellow
Set-Location $frontend
$frontendJob = Start-Process -PassThru -NoNewWindow `
    -FilePath "bun" `
    -ArgumentList "run", "dev"

# Détection de l'adresse IP locale sur le réseau
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.InterfaceAlias -notlike "*Loopback*" } | Select-Object -First 1).IPAddress
if (-not $ip) { $ip = "localhost" }

Write-Host "`nAudioBookStudio est lancé :" -ForegroundColor Cyan
Write-Host "  → Interface Web (Local) : http://localhost:5180"
Write-Host "  → Interface Web (Réseau): http://$($ip):5180"
Write-Host "  → API Backend           : http://localhost:17490"
Write-Host "`nCTRL+C pour arrêter proprement." -ForegroundColor Cyan

# Attente
try {
    while ($true) { Start-Sleep -Seconds 1 }
}
finally {
    Write-Host "`nArrêt de l'Orchestrateur..." -ForegroundColor Yellow
    if ($backendJob) { taskkill /PID $backendJob.Id /T /F | Out-Null }
    if ($frontendJob) { taskkill /PID $frontendJob.Id /T /F | Out-Null }
    Write-Host "OK. Tout est arrêté proprement." -ForegroundColor Green
}
