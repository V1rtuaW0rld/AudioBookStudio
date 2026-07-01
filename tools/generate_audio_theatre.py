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

def main(project_name, ranges_expr=None, voice_filter=None):
    project_dir = os.path.join(BASE_DIR, "Projects", project_name)
    tts_dir = os.path.join(project_dir, "tts")
    audio_dir = os.path.join(project_dir, "audio")

    os.makedirs(audio_dir, exist_ok=True)

    # Charger la table de mapping des voix depuis meta.json
    voice_mapping = {}
    meta_path = os.path.join(project_dir, "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
            voice_mapping = meta_data.get("voice_mapping", {})
        except Exception as e:
            print(f"[WARN] Impossible de lire meta.json : {e}")

    # Tous les segments JSON
    all_segments = sorted([
        f for f in os.listdir(tts_dir)
        if f.lower().endswith(".json") and f.lower().startswith("seg")
    ])

    if not all_segments:
        print(f"[ERREUR] Aucun segment JSON trouvé dans : {tts_dir}")
        sys.exit(1)

    # Filtre par plage
    if ranges_expr:
        wanted = parse_ranges(ranges_expr)
        all_segments = [
            f"seg{str(n).zfill(5)}.json"
            for n in wanted
            if f"seg{str(n).zfill(5)}.json" in all_segments
        ]

    print(f"[INFO] Segments candidats : {len(all_segments)}")

    for seg_file in all_segments:
        seg_path = os.path.join(tts_dir, seg_file)

        # Lire le JSON
        with open(seg_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        actor_name = data.get("profile_id", "NARRATOR")
        text = data.get("text", "")

        # Filtre de voix basé sur le rôle/personnage original
        if voice_filter and actor_name != voice_filter:
            continue

        if not text:
            print(f"[WARN] Segment vide : {seg_file}")
            continue

        # Résoudre la voix à partir du mapping, fallback sur le nom du personnage
        profile_id = voice_mapping.get(actor_name, actor_name)
        if profile_id == "DIDAS":
            profile_id = "Pierre Arditi" # Repli par défaut pour DIDAS si non mappé

        # Fichier texte temporaire
        tmp_txt = os.path.join(tts_dir, "tmp_text.txt")
        with open(tmp_txt, "w", encoding="utf-8") as f:
            f.write(text)

        # Fichier audio de sortie
        wav_name = seg_file.replace(".json", ".wav")
        wav_path = os.path.join(audio_dir, wav_name)

        print(f"[RUN] {seg_file} -> {wav_name} (voix : {profile_id} pour rôle : {actor_name})")

        # Appel à speak_text.py
        res = subprocess.run([
            sys.executable,
            os.path.join(BASE_DIR, "tools", "speak_text.py"),
            "--config", os.path.join(BASE_DIR, "config", "speak_config.json"),
            "--profile_id", profile_id,
            "--text", tmp_txt,
            "--output", wav_path
        ])

        if res.returncode == 0:
            data["generated_voice"] = profile_id
            try:
                with open(seg_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[WARN] Impossible de mettre à jour le JSON du segment : {e}")

    print("[OK] Génération audio terminée.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python generate_audio_theatre.py \"Projet\" [\"1-10,12,14-20\"] [\"Voix\"]")
        sys.exit(1)

    project_name = sys.argv[1]
    ranges = sys.argv[2] if len(sys.argv) >= 3 else None
    voice_filter = sys.argv[3] if len(sys.argv) >= 4 else None

    main(project_name, ranges, voice_filter)
