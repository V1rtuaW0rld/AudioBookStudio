import os
import sys
from pydub import AudioSegment

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def concat_audio(project_name):
    project_dir = os.path.join(BASE_DIR, "Projects", project_name)
    audio_dir = os.path.join(project_dir, "audio")

    if not os.path.exists(audio_dir):
        print(f"Erreur : dossier audio introuvable : {audio_dir}")
        sys.exit(1)

    # Récupérer tous les WAV triés alphanumériquement
    wav_files = sorted(
        [f for f in os.listdir(audio_dir) if f.lower().endswith(".wav")]
    )

    if not wav_files:
        print("Erreur : aucun fichier WAV trouvé.")
        sys.exit(1)

    print(f"[INFO] {len(wav_files)} fichiers WAV détectés.")
    print("[INFO] Concaténation en cours...")

    final_audio = AudioSegment.empty()

    for wav in wav_files:
        wav_path = os.path.join(audio_dir, wav)
        print(f"[ADD] {wav}")
        segment = AudioSegment.from_wav(wav_path)
        final_audio += segment

    # Nom du fichier final
    output_mp3 = os.path.join(project_dir, f"{project_name}.mp3")

    final_audio.export(output_mp3, format="mp3", bitrate="192k")

    print(f"[OK] Livre audio généré : {output_mp3}")
    print("[INFO] Tous les fichiers WAV ont été conservés.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python concat_2mp3_audio.py \"Nom du projet\"")
        sys.exit(1)

    concat_audio(sys.argv[1])
