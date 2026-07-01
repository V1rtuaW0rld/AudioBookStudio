import sys
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

if len(sys.argv) < 3:
    print("Usage: python extract_play.py <input.epub> <output.txt>")
    sys.exit(1)

epub_path = sys.argv[1]
output_path = sys.argv[2]

TARGET_PREFIX = ("thea",)  # fichiers théâtre

book = epub.read_epub(epub_path)
output_lines = []

def clean(text):
    return " ".join(text.split()).strip()

def is_duplicate(a, b):
    return a.lower() == b.lower()

for item in book.get_items():
    name = item.get_name().lower()

    # On ne garde que les fichiers théâtre
    if not name.endswith(".xhtml"):
        continue
    if not name.startswith(TARGET_PREFIX):
        continue

    soup = BeautifulSoup(item.get_content(), "html.parser")

    last_text = None

    for tag in soup.find_all(["h1", "h2", "h3", "p", "i"]):
        text = clean(tag.get_text())

        if not text:
            continue

        # Supprimer les doublons (p + i + span)
        if last_text and is_duplicate(text, last_text):
            continue

        last_text = text
        output_lines.append(text)

# Fusionner les paragraphes éclatés
final_lines = []
buffer = ""

for line in output_lines:
    if line.endswith((".", "!", "?", "…", ".)")):
        # Fin de phrase → on flush
        buffer += " " + line
        final_lines.append(buffer.strip())
        buffer = ""
    else:
        # Phrase coupée → on accumule
        buffer += " " + line

if buffer.strip():
    final_lines.append(buffer.strip())

with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n\n".join(final_lines))

print("Extraction terminée :", output_path)
