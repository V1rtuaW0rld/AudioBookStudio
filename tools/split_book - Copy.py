import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # audioBookStudio/


def find_book_file(book_dir):
    """
    Trouve le fichier .txt qui ne commence PAS par 'seg'.
    """
    for f in os.listdir(book_dir):
        if f.lower().endswith(".txt") and not f.lower().startswith("seg"):
            return os.path.join(book_dir, f)
    return None


def split_book(project_name):
    # Construire le chemin du projet
    project_dir = os.path.join(BASE_DIR, "Projects", project_name)
    book_dir = os.path.join(project_dir, "book")
    tts_dir = os.path.join(project_dir, "tts")

    os.makedirs(tts_dir, exist_ok=True)

    if not os.path.exists(book_dir):
        print(f"Erreur : dossier introuvable : {book_dir}")
        sys.exit(1)

    # Trouver le fichier source
    input_file = find_book_file(book_dir)
    if not input_file:
        print("Erreur : aucun fichier .txt source trouvé dans /book (hors segXXXXX.txt).")
        sys.exit(1)

    print(f"[INFO] Fichier détecté : {input_file}")

    # Lire le fichier et regrouper par paragraphes
    paragraphs = []
    current = []

    with open(input_file, "r", encoding="utf-8") as f:
        for raw in f.readlines():
            line = raw.strip()

            if line == "":
                # Fin d’un paragraphe
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
            else:
                current.append(line)

    # Dernier paragraphe si non vide
    if current:
        paragraphs.append(" ".join(current))

    if not paragraphs:
        print("Erreur : aucun paragraphe détecté.")
        sys.exit(1)

    print(f"[INFO] {len(paragraphs)} paragraphes détectés. Nettoyage des anciens segments...")

    # Nettoyage des anciens fichiers segments (json, txt, wav)
    # 1. Supprimer tts/seg*.json
    if os.path.exists(tts_dir):
        for f in os.listdir(tts_dir):
            if f.startswith("seg") and f.endswith(".json"):
                try:
                    os.remove(os.path.join(tts_dir, f))
                except Exception:
                    pass
    # 2. Supprimer book/seg*.txt
    if os.path.exists(book_dir):
        for f in os.listdir(book_dir):
            if f.startswith("seg") and f.endswith(".txt"):
                try:
                    os.remove(os.path.join(book_dir, f))
                except Exception:
                    pass
    print("[INFO] Découpage en cours...")

    # Générer les segments : 1 paragraphe = 1 segment
    for i, text in enumerate(paragraphs, start=1):
        seg_data = {
            "profile_id": "Narrator",
            "text": text
        }
        seg_name = f"seg{str(i).zfill(5)}.json"
        seg_path = os.path.join(tts_dir, seg_name)

        with open(seg_path, "w", encoding="utf-8") as out:
            json.dump(seg_data, out, ensure_ascii=False, indent=2)

    print(f"[OK] Segments générés dans : {tts_dir}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python split_book.py \"Nom du projet\"")
        sys.exit(1)

    project_name = sys.argv[1]
    split_book(project_name)
