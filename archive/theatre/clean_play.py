import sys
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import re

if len(sys.argv) < 3:
    print("Usage: python extract_play.py <input.epub> <output.txt>")
    sys.exit(1)

epub_path = sys.argv[1]
output_path = sys.argv[2]

TARGET_PREFIX = ("thea",)

book = epub.read_epub(epub_path)
output_lines = []

def clean(text):
    return " ".join(text.split()).strip()

def is_character_name(text):
    return text.isupper() and 1 < len(text) < 40

for item in book.get_items():
    name = item.get_name().lower()
    if not name.endswith(".xhtml"):
        continue
    if not name.startswith(TARGET_PREFIX):
        continue

    soup = BeautifulSoup(item.get_content(), "html.parser")

    last_text = None

    for tag in soup.find_all(["h1", "h2", "h3", "p", "i"]):
        raw = tag.get_text()
        text = clean(raw)

        if not text:
            continue

        # Supprimer doublons exacts
        if last_text and text.lower() == last_text.lower():
            continue
        last_text = text

        # Didascalies (balise <i>)
        if tag.name == "i":
            output_lines.append(f"[DIDASCALIE] {text}")
            continue

        # Séparer NOM + réplique
        parts = text.split(" ", 1)
        if len(parts) == 2 and is_character_name(parts[0]):
            name, rest = parts
            output_lines.append(name)
            output_lines.append(rest)
            continue

        # Nom seul
        if is_character_name(text):
            output_lines.append(text)
            continue

        # Texte normal
        output_lines.append(text)

# Fusionner les paragraphes éclatés
final_lines = []
buffer = ""

for line in output_lines:
    if line.startswith("[DIDASCALIE]"):
        if buffer.strip():
            final_lines.append(buffer.strip())
            buffer = ""
        final_lines.append(line)
        continue

    if line.endswith((".", "!", "?", "…", ".)")):
        buffer += " " + line
        final_lines.append(buffer.strip())
        buffer = ""
    else:
        buffer += " " + line

if buffer.strip():
    final_lines.append(buffer.strip())

with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n\n".join(final_lines))

print("Extraction terminée :", output_path)
