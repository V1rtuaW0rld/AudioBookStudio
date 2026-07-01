import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # audioBookStudio/


def find_theatre_file(book_dir):
    """
    Trouve le fichier .txt source pour la pièce, en priorisant _chunked.txt,
    puis _parsed.txt, et enfin n'importe quel .txt (non seg).
    """
    # 1. Priorité au fichier découpé en chunks
    for f in os.listdir(book_dir):
        if f.lower().endswith("_chunked.txt"):
            return os.path.join(book_dir, f)
            
    # 2. Puis au fichier parsé
    for f in os.listdir(book_dir):
        if f.lower().endswith("_parsed.txt"):
            return os.path.join(book_dir, f)
            
    # 3. Repli sur le premier fichier .txt brut
    for f in os.listdir(book_dir):
        if f.lower().endswith(".txt") and not f.lower().startswith("seg"):
            return os.path.join(book_dir, f)
            
    return None


def split_theatre(project_name):
    project_dir = os.path.join(BASE_DIR, "Projects", project_name)
    book_dir = os.path.join(project_dir, "book")
    tts_dir = os.path.join(project_dir, "tts")

    os.makedirs(tts_dir, exist_ok=True)

    if not os.path.exists(book_dir):
        print(f"Erreur : dossier introuvable : {book_dir}")
        sys.exit(1)

    input_file = find_theatre_file(book_dir)
    if not input_file:
        print("Erreur : aucun fichier .txt source trouvé dans /book (hors segXXXXX.txt).")
        sys.exit(1)

    print(f"[INFO] Fichier détecté : {input_file}")

    # Lire toutes les lignes
    with open(input_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]

    if len(lines) % 2 != 0:
        print("[WARN] Nombre impair de lignes : dernière ligne ignorée.")
        lines = lines[:-1]

    print(f"[INFO] {len(lines)//2} segments à générer. Nettoyage des anciens segments...")

    # Nettoyage des anciens fichiers segments (json, wav)
    # 1. Supprimer tts/seg*.json
    if os.path.exists(tts_dir):
        for f in os.listdir(tts_dir):
            if f.startswith("seg") and f.endswith(".json"):
                try:
                    os.remove(os.path.join(tts_dir, f))
                except Exception:
                    pass
    print("[INFO] Découpage en cours...")

    seg_index = 1

    for i in range(0, len(lines), 2):
        actor = lines[i]
        text = lines[i+1]
    
        # Conserver le personnage original (normalisé en majuscule avec apostrophe droite)
        profile = actor.strip().upper().replace("’", "'")
    
        # Nettoyer les parenthèses extérieures pour les didascalies (profile == "DIDAS")
        if profile == "DIDAS":
            cleaned_text = text.strip()
            if cleaned_text.startswith("(") and cleaned_text.endswith(")"):
                text = cleaned_text[1:-1].strip()

        seg_data = {
            "profile_id": profile,
            "text": text
        }
    
        seg_name = f"seg{str(seg_index).zfill(5)}.json"
        seg_path = os.path.join(tts_dir, seg_name)
    
        with open(seg_path, "w", encoding="utf-8") as out:
            json.dump(seg_data, out, ensure_ascii=False, indent=2)
    
        seg_index += 1


    print(f"[OK] Segments JSON générés dans : {tts_dir}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python split_theatre.py \"NomDuProjet\"")
        sys.exit(1)

    split_theatre(sys.argv[1])
