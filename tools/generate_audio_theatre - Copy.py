import os
import sys
import json
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main(project_name, ranges_expr=None):
    project_dir = os.path.join(BASE_DIR, "Projects", project_name)
    tts_dir = os.path.join(project_dir, "tts")
    audio_dir = os.path.join(project_dir, "audio")

    os.makedirs(audio_dir, exist_ok=True)

    # Récupérer tous les segments JSON
    all_segments = sorted([
        f for f in os.listdir(tts_dir)
        if f.lower().endswith(".json") and f.lower().startswith("seg")
    ])

    if not all_segments:
        print(f"[ERREUR] Aucun segment JSON trouvé dans : {tts_dir}")
        sys.exit(1)

    # Si l'utilisateur fournit une plage : 1-10,12,14-20
    if ranges_expr:
        wanted = parse_ranges(ranges_expr)
        all_segments = [
            f"seg{str(n).zfill(5)}.json"
            for n in wanted
            if f"seg{str(n).zfill(5)}.json" in all_segments
        ]

    print(f"[INFO] Segments à générer : {len(all_segments)}")

    for seg_file in all_segments:
        seg_path = os.path.join(tts_dir, seg_file)

        # Lire le JSON
        with open(seg_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        profile_id = data.get("profile_id", "NARRATOR")
        text = data.get("text", "")

        if not text:
            print(f"[WARN] Segment vide : {seg_file}")
            continue

        # Créer un fichier texte temporaire
        tmp_txt = os.path.join(tts_dir, "tmp_text.txt")
        with open(tmp_txt, "w", encoding="utf-8") as f:
            f.write(text)

        # Fichier audio de sortie
        wav_name = seg_file.replace(".json", ".wav")
        wav_path = os.path.join(audio_dir, wav_name)

        print(f"[RUN] {seg_file} → {wav_name} (voix : {profile_id})")

        # Appel à speak_text.py
        subprocess.run([
            sys.executable,
            os.path.join(BASE_DIR, "tools", "speak_text.py"),
            "--config", os.path.join(BASE_DIR, "config", "speak_config.json"),
            "--profile_id", profile_id,
            "--text", tmp_txt,
            "--output", wav_path
        ])

    print("[OK] Génération audio terminée.")


def parse_ranges(expr):
    result = []
    parts = expr.split(",")
    for part in parts:
        part = part.strip()
        if "-" in part:
            start, end = part.split("-")
            result.extend(range(int(start), int(end) + 1))
        else:
            result.append(int(part))
    return sorted(set(result))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python generate_audio_theatre.py \"NomDuProjet\" [\"1-10,12,14-20\"]")
        sys.exit(1)

    project_name = sys.argv[1]
    ranges = sys.argv[2] if len(sys.argv) >= 3 else None

    main(project_name, ranges)
