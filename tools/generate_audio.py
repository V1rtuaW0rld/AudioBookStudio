import os
import sys
import json
import subprocess

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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

def main(project_name, ranges_expr, profile_id):
    project_dir = os.path.join(BASE_DIR, "Projects", project_name)
    tts_dir = os.path.join(project_dir, "tts")
    audio_dir = os.path.join(project_dir, "audio")

    os.makedirs(audio_dir, exist_ok=True)

    segments = parse_ranges(ranges_expr)

    print(f"[INFO] Segments demandés : {segments}")

    for n in segments:
        json_file = os.path.join(tts_dir, f"seg{str(n).zfill(5)}.json")
        wav_file = os.path.join(audio_dir, f"seg{str(n).zfill(5)}.wav")

        if not os.path.exists(json_file):
            print(f"[WARN] Segment introuvable : {json_file}")
            continue

        # Lire le JSON
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        current_profile = profile_id or data.get("profile_id", "Narrator")
        text = data.get("text", "")

        if not text:
            print(f"[WARN] Segment vide : {json_file}")
            continue

        # Créer un fichier texte temporaire pour speak_text.py
        tmp_txt = os.path.join(tts_dir, "tmp_text.txt")
        with open(tmp_txt, "w", encoding="utf-8") as f:
            f.write(text)

        print(f"[RUN] Génération : {json_file} -> {wav_file} (voix : {current_profile})")

        res = subprocess.run([
            sys.executable,
            os.path.join(BASE_DIR, "tools", "speak_text.py"),
            "--config", os.path.join(BASE_DIR, "config", "speak_config.json"),
            "--profile_id", current_profile,
            "--text", tmp_txt,
            "--output", wav_file
        ])

        if res.returncode == 0:
            data["profile_id"] = current_profile
            data["generated_voice"] = current_profile
            try:
                with open(json_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[WARN] Impossible de mettre à jour le JSON du segment : {e}")

    print("[OK] Génération terminée.")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage : python generate_audio.py \"Nom du projet\" \"1-10,12,13-17\" \"Profil\"")
        sys.exit(1)

    main(sys.argv[1], sys.argv[2], sys.argv[3])
