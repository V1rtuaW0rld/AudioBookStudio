# ============================================================
# AudioBookStudio — Importateur de pack de voix VoiceBox
# ============================================================
param(
    [string]$ZipPath = "AudioBookStudio-French-Voices-v1.0.zip"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path $ZipPath)) {
    Write-Host "`n[ERREUR] Fichier introuvable : $ZipPath" -ForegroundColor Red
    Write-Host "Veuillez télécharger le pack de voix depuis le lien Google Drive :" -ForegroundColor Yellow
    Write-Host "  -> https://drive.google.com/file/d/15ck6aUZO5yD_5G8IthBom7ZQNaHwWzEP/view?usp=sharing" -ForegroundColor Cyan
    Write-Host "Placez le fichier zip téléchargé à la racine du projet, puis relancez ce script.`n" -ForegroundColor Yellow
    exit 1
}

Write-Host "`nExtraction du pack de voix dans VoiceBox/data..." -ForegroundColor Cyan
Expand-Archive -Path $ZipPath -DestinationPath "VoiceBox/data" -Force

Write-Host "[SUCCÈS] 80+ profils de voix françaises installés dans VoiceBox/data !" -ForegroundColor Green
Write-Host "Vous pouvez maintenant lancer : .\run-docker.ps1`n" -ForegroundColor Green
