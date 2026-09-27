<#
==============================================================================
 shift_segments.ps1  —  Renommage/décalage en masse des segments AudioBookStudio
==============================================================================
 Décale les fichiers segNNNNN.<ext> d'un intervalle [Start..End] de `Delta`.
 Ex. Delta = -1 : seg00445 -> seg00444, seg00446 -> seg00445, ... (445 écrase 444).
     Delta = +1 : décale vers le haut (parcours automatiquement inversé).

 IMPORTANT
 - Opération IRRÉVERSIBLE (écrase les destinations). Une sauvegarde horodatée
   est faite automatiquement (sauf -NoBackup).
 - Par défaut : DRY-RUN (montre ce qui serait fait sans rien modifier).
   Ajoute -Execute pour appliquer réellement.
 - Choisis les types de fichiers via -Ext (wav, json, txt). Décaler « wav » seul
   corrige un désalignement audio↔texte ; décaler les 3 garde le projet cohérent.

 EXEMPLES
   # Aperçu (dry-run) d'un décalage -1 des WAV de 445 à 713 :
   .\shift_segments.ps1 -Project "Marcel Pagnol - La gloire de mon père" `
                        -Start 445 -End 713 -Delta -1 -Ext wav

   # Appliquer réellement :
   .\shift_segments.ps1 -Project "Marcel Pagnol - La gloire de mon père" `
                        -Start 445 -End 713 -Delta -1 -Ext wav -Execute

   # Décalage synchrone wav + json + txt :
   .\shift_segments.ps1 -Project "..." -Start 445 -End 713 -Delta -1 `
                        -Ext wav,json,txt -Execute
==============================================================================
#>

param(
    # Nom du projet (dossier sous Projects\) OU chemin complet du projet.
    [Parameter(Mandatory = $true)] [string]   $Project,
    [Parameter(Mandatory = $true)] [int]      $Start,     # premier N source à décaler
    [Parameter(Mandatory = $true)] [int]      $End,       # dernier N source
    [int]      $Delta = -1,                                # décalage (négatif ou positif)
    [string[]] $Ext   = @("wav"),                         # types : wav / json / txt
    [switch]   $Execute,                                   # sans ce flag => DRY-RUN
    [switch]   $NoBackup                                   # désactive la sauvegarde auto
)

# --- Résolution des chemins --------------------------------------------------
$ProjectsRoot = "C:\Applications\audioBookStudio\Projects"
if (Test-Path -LiteralPath $Project) {
    $ProjectDir = (Resolve-Path -LiteralPath $Project).Path
} else {
    $ProjectDir = Join-Path $ProjectsRoot $Project
}
if (-not (Test-Path -LiteralPath $ProjectDir)) {
    Write-Error "Projet introuvable : $ProjectDir"; exit 1
}

# ext -> sous-dossier correspondant
$SubDir = @{ "wav" = "audio"; "json" = "tts"; "txt" = "book" }

if ($Delta -eq 0) { Write-Error "Delta = 0 : rien à faire."; exit 1 }

# Sens de parcours : Delta<0 => croissant ; Delta>0 => décroissant.
# (évite d'écraser une source pas encore traitée)
$range = if ($Delta -lt 0) { $Start..$End } else { $End..$Start }

Write-Host ""
Write-Host "Projet : $ProjectDir"
Write-Host ("Décalage : {0:+#;-#} sur segments {1}..{2}  (types : {3})" -f $Delta, $Start, $End, ($Ext -join ', '))
Write-Host ("Mode : {0}" -f ($(if ($Execute) { 'EXECUTION' } else { 'DRY-RUN (aperçu, rien modifié)' })))
Write-Host ""

foreach ($e in $Ext) {
    if (-not $SubDir.ContainsKey($e)) { Write-Warning "Extension inconnue ignorée : $e"; continue }
    $dir = Join-Path $ProjectDir $SubDir[$e]
    if (-not (Test-Path -LiteralPath $dir)) { Write-Warning "Dossier absent : $dir"; continue }

    Write-Host "--- $e  ($dir) ---"

    # Sauvegarde horodatée du dossier concerné (une seule fois, avant modif).
    if ($Execute -and -not $NoBackup) {
        $stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
        $backup = "$dir`_backup_$stamp"
        Copy-Item -LiteralPath $dir -Destination $backup -Recurse
        Write-Host "  Sauvegarde : $backup"
    }

    $moved = 0; $missing = 0
    foreach ($i in $range) {
        $src = Join-Path $dir ("seg{0:D5}.{1}" -f $i, $e)
        $dst = Join-Path $dir ("seg{0:D5}.{1}" -f ($i + $Delta), $e)
        if (Test-Path -LiteralPath $src) {
            if ($Execute) {
                Move-Item -LiteralPath $src -Destination $dst -Force
            }
            Write-Host ("  seg{0:D5}.{1} -> seg{2:D5}.{1}" -f $i, $e, ($i + $Delta))
            $moved++
        } else {
            $missing++
        }
    }
    Write-Host ("  => {0} fichier(s) {1}, {2} absent(s)." -f $moved, ($(if ($Execute) { 'déplacé(s)' } else { 'à déplacer' })), $missing)
    Write-Host ""
}

if (-not $Execute) {
    Write-Host "Aperçu terminé. Relance avec -Execute pour appliquer." -ForegroundColor Yellow
} else {
    Write-Host "Terminé." -ForegroundColor Green
    Write-Host "Rappel : si tu as décalé les WAV, re-score les segments concernés (les qc_score des JSON correspondent aux anciens audios)." -ForegroundColor Yellow
}
