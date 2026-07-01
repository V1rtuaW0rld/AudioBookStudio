import argparse
import os
import re
import sys
import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

TARGET = 180
SOFT_MAX = 220
HARD_MAX = 800

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # audioBookStudio/


def split_sentences(text: str):
    # La regex corrigée : on inclut le point (\.) dans le lookbehind !
    pattern = r"""
        (?<!\b[A-Za-zÀ-ÿ]\.)  # NE PAS couper si 1 lettre + point (ex: P., M., É., J.)
        (?<!\bMme\.)          # NE PAS couper après Mme.
        (?<!\bMlle\.)         # NE PAS couper après Mlle.
        (?<!\bDr\.)           # NE PAS couper après Dr.
        (?<!\betc\.)          # NE PAS couper après etc.
        (?<=[.!?…])           # Couper APRÈS . ! ? ou …
        \s+                   # Couper sur les espaces qui suivent
    """

    parts = re.split(pattern, text.strip(), flags=re.VERBOSE)
    merged = []
    skip_next = False

    for i in range(len(parts)):
        if skip_next:
            skip_next = False
            continue

        p = parts[i].strip()
        if not p:
            continue

        # Gestion des points de suspension "…" en milieu de phrase
        if p.endswith("…") and i + 1 < len(parts):
            nxt = parts[i + 1].lstrip()
            # Si la suite ne commence pas par une majuscule, on fusionne
            if nxt and not re.match(r"^[A-ZÀ-Ÿ][a-zà-ÿ]", nxt):
                merged.append(p + " " + nxt)
                skip_next = True
                continue

        merged.append(p)

    return merged


# --- 2. Construire des lignes intelligentes ---
def build_smart_lines(sentences):
    lines = []
    current = ""

    for s in sentences:
        s = s.strip()
        if not s:
            continue

        if len(s) > SOFT_MAX:
            if current:
                lines.append(current)
                current = ""
            lines.append(s)
            continue

        if not current:
            current = s
            continue

        candidate_len = len(current) + 1 + len(s)

        if candidate_len <= TARGET:
            current = current + " " + s
            continue

        if candidate_len <= SOFT_MAX:
            current = current + " " + s
            continue

        lines.append(current)
        current = s

        if len(current) > HARD_MAX:
            lines.append(current)
            current = ""

    if current:
        lines.append(current)

    return lines


# --- 3. Trouver automatiquement le fichier EPUB ---
def find_epub(project_name):
    book_dir = os.path.join(BASE_DIR, "Projects", project_name, "book")

    if not os.path.exists(book_dir):
        print(f"[ERREUR] Dossier introuvable : {book_dir}")
        sys.exit(1)

    for f in os.listdir(book_dir):
        if f.lower().endswith(".epub") and not f.lower().startswith("seg"):
            return os.path.join(book_dir, f)

    print("[ERREUR] Aucun fichier EPUB trouvé dans /book")
    sys.exit(1)


# --- 4. Extraction EPUB → TXT ---
def extract_epub_to_txt(epub_path: str, output_path: str):
    try:
        book = epub.read_epub(epub_path)
        blocks = []

        print(f"[*] Extraction : {epub_path}")

        for item in book.get_items():
            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue

            html = item.get_content().decode("utf-8", errors="ignore")
            soup = BeautifulSoup(html, "html.parser")

            # --- TITRES ---
            for h in soup.find_all(["h1", "h2", "h3", "h4"]):
                t = h.get_text(separator=" ", strip=True)
                if t:
                    blocks.append(("TITLE", f"=== {t} ==="))

            # --- TEXTE BRUT ---
            raw_text = soup.get_text(separator=" ")

            # Nettoyage des espaces multiples pour transformer le texte en flux pur
            clean_text = re.sub(r"\s+", " ", raw_text).strip()

            if clean_text:
                blocks.append(("TEXT", clean_text))

        # --- 5. Pipeline final ---
        final_lines = []

        for kind, content in blocks:
            if kind == "TITLE":
                final_lines.append(content)
                final_lines.append("")
                continue

            if kind == "TEXT":
                sentences = split_sentences(content)
                smart = build_smart_lines(sentences)
                final_lines.extend(smart)
                final_lines.append("")

        # Nettoyage final des lignes vides
        cleaned = []
        for line in final_lines:
            if line.strip() == "" and (not cleaned or cleaned[-1].strip() == ""):
                continue
            cleaned.append(line)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(cleaned))

        print(f"[OK] Fichier généré : {output_path}")

    except Exception as e:
        print(f"[ERREUR] {e}", file=sys.stderr)


# --- CLI ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="EPUB → TXT (universel, spécial audiobook narrateur unique)."
    )
    parser.add_argument(
        "--project", required=True, help="Nom du projet (répertoire sous Projects)"
    )
    args = parser.parse_args()

    epub_path = find_epub(args.project)

    base, _ = os.path.splitext(epub_path)
    output_path = base + ".txt"

    extract_epub_to_txt(epub_path, output_path)