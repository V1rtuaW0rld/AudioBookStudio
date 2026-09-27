import os
import re
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

def main(project_name, ranges_expr, profile_id, voice=None):
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

        # Rôle (étiquette) et voix de synthèse peuvent différer : un roman
        # multi-voix garde le rôle (ex. EVA) tout en synthétisant avec une voix
        # dédiée (ex. Julia Roberts). Rétro-compatible : sans `voice`, la voix =
        # le profil passé (comportement mono-narrateur d'origine).
        role = profile_id or data.get("profile_id", "Narrator")
        speak_voice = voice or role
        text = data.get("text", "")

        if not text:
            print(f"[WARN] Segment vide : {json_file}")
            continue

        # Normalisation de l'ENTRÉE TTS uniquement (le texte stocké/affiché reste
        # intact). Une ponctuation « suspendue » en fin de segment (deux-points,
        # tiret, guillemet ouvrant/fermant…) — typique des amorces narrateur dont
        # la réplique est dans le segment suivant — fait DÉRAILLER ou TRONQUER le
        # modèle TTS autorégressif (il « attend » une suite qui ne vient jamais).
        # On la remplace par un point (pause équivalente à l'oreille).
        tts_text = re.sub(r'[\s:;,\-–—«»"“”\'’]+$', '', text.strip())
        if tts_text and tts_text[-1] not in '.!?…':
            tts_text += '.'
        if not tts_text:
            tts_text = text.strip()
        if tts_text != text.strip():
            print(f"[INFO] Ponctuation finale normalisée pour le TTS : …{text.strip()[-25:]!r} -> …{tts_text[-25:]!r}")

        # Créer un fichier texte temporaire pour speak_text.py
        tmp_txt = os.path.join(tts_dir, "tmp_text.txt")
        with open(tmp_txt, "w", encoding="utf-8") as f:
            f.write(tts_text)

        print(f"[RUN] Génération : {json_file} -> {wav_file} (rôle : {role}, voix : {speak_voice})")

        res = subprocess.run([
            sys.executable,
            os.path.join(BASE_DIR, "tools", "speak_text.py"),
            "--config", os.path.join(BASE_DIR, "config", "speak_config.json"),
            "--profile_id", speak_voice,
            "--text", tmp_txt,
            "--output", wav_file
        ])

        if res.returncode == 0:
            data["profile_id"] = role
            data["generated_voice"] = speak_voice
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

    voice_arg = sys.argv[4] if len(sys.argv) > 4 else None
    main(sys.argv[1], sys.argv[2], sys.argv[3], voice_arg)
