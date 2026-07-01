import sys
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

if len(sys.argv) < 2:
    print("Usage: python structure.py <fichier.epub>")
    sys.exit(1)

epub_path = sys.argv[1]

TARGET_PREFIX = ("thea", "theatre", "scene")

book = epub.read_epub(epub_path)

for item in book.get_items():
    name = item.get_name().lower()

    # On ne garde que les fichiers théâtre
    if not name.endswith(".xhtml"):
        continue
    if not name.startswith(TARGET_PREFIX):
        continue

    print(f"\n=== FILE: {item.get_name()} ===")
    soup = BeautifulSoup(item.get_content(), "html.parser")

    for tag in soup.find_all(["h1","h2","h3","p","i","span"]):
        text = tag.get_text().strip().replace("\n", " ")
        print(f"{tag.name:4} | {text[:120]}")
