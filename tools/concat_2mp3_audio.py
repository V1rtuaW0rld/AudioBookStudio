import os
import sys
from pydub import AudioSegment

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def parse_ranges(expr):
    result = []
    if not expr:
        return result
    parts = expr.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start, end = part.split("-")
                result.extend(range(int(start), int(end) + 1))
            except ValueError:
                pass
        else:
            try:
                result.append(int(part))
            except ValueError:
                pass
    return sorted(set(result))

def concat_audio(project_name, ranges_expr=None, output_name=None):
    project_dir = os.path.join(BASE_DIR, "Projects", project_name)
    audio_dir = os.path.join(project_dir, "audio")

    if not os.path.exists(audio_dir):
        print(f"Erreur : dossier audio introuvable : {audio_dir}")
        sys.exit(1)

    # Tous les WAV triés
    wav_files = sorted(
        [f for f in os.listdir(audio_dir) if f.lower().endswith(".wav")]
    )

    if not wav_files:
        print("Erreur : aucun fichier WAV trouvé.")
        sys.exit(1)

    # Si une plage est fournie : filtrer
    if ranges_expr and ranges_expr.strip() and ranges_expr.strip().lower() != "all":
        wanted = parse_ranges(ranges_expr)
        wav_files = [
            f"seg{str(n).zfill(5)}.wav"
            for n in wanted
            if f"seg{str(n).zfill(5)}.wav" in wav_files
        ]

    if not wav_files:
        print("Erreur : aucun fichier WAV correspondant à la plage.")
        sys.exit(1)

    print(f"[INFO] {len(wav_files)} fichiers WAV à concaténer.")
    print("[INFO] Concaténation en cours...")

    final_audio = AudioSegment.empty()

    for wav in wav_files:
        wav_path = os.path.join(audio_dir, wav)
        print(f"[ADD] {wav}")
        segment = AudioSegment.from_wav(wav_path)
        final_audio += segment

    # Nom du fichier final
    if output_name:
        output_mp3 = os.path.join(project_dir, output_name)
    else:
        output_mp3 = os.path.join(project_dir, f"{project_name}.mp3")

    final_audio.export(output_mp3, format="mp3", bitrate="192k")

    print(f"[OK] Fichier généré : {output_mp3}")
    print("[INFO] Tous les fichiers WAV ont été conservés.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python concat_2mp3_audio.py \"Projet\" [\"1-200\"] [\"NomSortie.mp3\"]")
        sys.exit(1)

    project_name = sys.argv[1]
    ranges_expr = sys.argv[2] if len(sys.argv) >= 3 else None
    output_name = sys.argv[3] if len(sys.argv) >= 4 else None

    concat_audio(project_name, ranges_expr, output_name)
