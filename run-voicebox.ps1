# ================================
#  VoiceBox Launcher (just dev-web)
# ================================

Write-Host "=== VoiceBox Launcher ===" -ForegroundColor Cyan

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$voicebox = Join-Path $root "VoiceBox"

# Aller dans VoiceBox
Set-Location $voicebox

Write-Host "Démarrage de VoiceBox (just dev-web)..." -ForegroundColor Yellow

# Lancer just dev-web en arrière-plan
$job = Start-Process -PassThru -NoNewWindow `
    -FilePath "just" `
    -ArgumentList "dev-web"

Write-Host "`nVoiceBox est lancé :" -ForegroundColor Cyan
Write-Host "  → Frontend : http://localhost:5173"
Write-Host "  → Backend  : http://localhost:17493"
Write-Host "`nCTRL+C pour arrêter proprement." -ForegroundColor Cyan

# Attente
try {
    while ($true) { Start-Sleep -Seconds 1 }
}
finally {
    Write-Host "`nArrêt de VoiceBox..." -ForegroundColor Yellow
    if ($job) { taskkill /PID $job.Id /T /F | Out-Null }
    Write-Host "OK. Tout est arrêté proprement." -ForegroundColor Green
}
