import re
import os
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import argparse
import sys

TARGET = 180
SOFT_MAX = 220
HARD_MAX = 800


# --- 1. Découper proprement en phrases ---
def split_sentences(text: str):
    # 1) On coupe sur . ! ? ; mais PAS sur …
    parts = re.split(r'(?<=[\.\!\?;])\s+', text.strip())

    merged = []
    skip_next = False

    for i in range(len(parts)):
        if skip_next:
            skip_next = False
            continue

        p = parts[i].strip()

        # Si la phrase se termine par "…" → ce n'est PAS une fin de phrase
        if p.endswith("…") and i + 1 < len(parts):
            nxt = parts[i + 1].lstrip()

            # Si la phrase suivante NE commence PAS par une majuscule suivie d'une minuscule
            # → ce n'est PAS une nouvelle phrase
            if not re.match(r'^[A-Z][a-z]', nxt):
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

        # Phrase très longue → ligne seule
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

        # Cas 1 : dans la cible idéale
        if candidate_len <= TARGET:
            current = current + " " + s
            continue

        # Cas 2 : dépasse TARGET mais reste acceptable
        if candidate_len <= SOFT_MAX:
            current = current + " " + s
            continue

        # Cas 3 : trop long → couper
        lines.append(current)
        current = s

        # Sécurité absolue
        if len(current) > HARD_MAX:
            lines.append(current)
            current = ""

    if current:
        lines.append(current)

    return lines


# --- 3. Extraction EPUB ---
def extract_epub_to_txt(epub_path: str, output_path: str):
    try:
        book = epub.read_epub(epub_path)
        blocks = []

        print(f"[*] Extraction : {epub_path}")

        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                html = item.get_content().decode("utf-8", errors="ignore")
                soup = BeautifulSoup(html, "html.parser")

                # TITRES : toujours en blocs séparés
                for h in soup.find_all(["h1", "h2", "h3", "h4"]):
                    t = h.get_text().strip()
                    if t:
                        blocks.append(("TITLE", f"=== {t} ==="))

                # PARAGRAPHES
                for p in soup.find_all("p"):
                    txt = p.get_text().strip()
                    if txt:
                        blocks.append(("TEXT", txt))

        final_lines = []

        # Traitement bloc par bloc
        for kind, content in blocks:

            if kind == "TITLE":
                final_lines.append(content)
                final_lines.append("")  # saut de ligne
                continue

            if kind == "TEXT":
                sentences = split_sentences(content)
                smart = build_smart_lines(sentences)
                final_lines.extend(smart)
                final_lines.append("")  # saut de ligne entre paragraphes

        # Nettoyage final : pas de lignes vides multiples
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
    parser = argparse.ArgumentParser(description="EPUB → TXT (format spécial audiobook).")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    # Sortie automatique à côté du .epub
    if args.output:
        output_path = args.output
    else:
        base, _ = os.path.splitext(args.source)
        output_path = base + ".txt"

    extract_epub_to_txt(args.source, output_path)
