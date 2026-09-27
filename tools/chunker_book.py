import sys
import re

MIN_CHARS = 400
MAX_CHARS = 500

IDEAL_PUNCT = [".", "!", "?"]
ABBREVIATIONS = ["M.", "MM.", "Mme.", "Mlle.", "Mr.", "Mrs.", "Ms.", "Dr.", "Pr.",
                 "St.", "Ste.", "etc.", "cf.", "J.-C."]
CLOSING_QUOTES = ["»", "”", "\"", "’", "'"]


def is_abbreviation(text, pos):
    """Vérifie si la ponctuation est en fait une abréviation."""
    for abbr in ABBREVIATIONS:
        if pos - len(abbr) + 1 >= 0 and text[pos - len(abbr) + 1:pos + 1] == abbr:
            return True
    return False


def is_initial(text, pos):
    """Vérifie si le point est une initiale isolée (ex: A., J.)."""
    return bool(re.search(r'\b[A-Za-zÀ-ÿ]\.$', text[:pos + 1]))


def get_quote_offset(text, pos):
    """Vérifie si un guillemet fermant suit la ponctuation (avec espace éventuel)."""
    lookahead = text[pos + 1:pos + 5]
    skip = 0
    for ch in lookahead:
        if ch in (" ", "\xa0"):
            skip += 1
            continue
        if ch in CLOSING_QUOTES:
            return skip + 1
        break
    return 0


def find_best_punct_in_range(text, start, end):
    """Cherche la dernière ponctuation stricte (. ! ?) valide dans text[start:end]."""
    best = -1
    for p in IDEAL_PUNCT:
        pos = text.rfind(p, start, end)
        while pos != -1:
            if not is_abbreviation(text, pos) and not is_initial(text, pos):
                offset = get_quote_offset(text, pos)
                cut = pos + offset
                if cut > best:
                    best = cut
                break
            pos = text.rfind(p, start, pos)
    return best


def find_cut_position(text, min_size=MIN_CHARS, max_size=MAX_CHARS):
    """Trouve la meilleure position de coupure stricte (. ! ? uniquement)."""
    # 1. Priorité 1 : Fin de phrase stricte dans la plage idéale [min_size, max_size]
    cut = find_best_punct_in_range(text, min_size, max_size)
    if cut > 0:
        return cut

    # 2. Si aucune fin de phrase dans [min_size, max_size] :
    #    On cherche une fin de phrase un peu avant min_size (jusqu'à 200 car. plus tôt)
    cut = find_best_punct_in_range(text, max(0, min_size - 200), min_size)
    if cut > 0:
        return cut

    # 3. Si la phrase est longue et dépasse max_size : on cherche un peu après max_size (jusqu'à +150 car.)
    cut = find_best_punct_in_range(text, max_size, min(len(text), max_size + 150))
    if cut > 0:
        return cut

    # 4. Recherche élargie dès le début si nécessaire
    cut = find_best_punct_in_range(text, 0, max(0, min_size - 200))
    if cut > 0:
        return cut

    # 5. Dernier recours exceptionnel (phrase > 650 car. sans aucun . ! ?) : couper sur le dernier espace
    pos = text.rfind(" ", min_size, max_size)
    if pos != -1:
        return pos

    return max_size


def chunk_text(text, min_size=MIN_CHARS, max_size=MAX_CHARS):
    """Découpe un bloc de texte en chunks propres, sans jamais couper sur un espace."""
    chunks = []
    current = text.strip()

    while len(current) > max_size:
        cut_pos = find_cut_position(current, min_size, max_size)
        chunk = current[:cut_pos + 1].strip()
        chunks.append(chunk)
        current = current[cut_pos + 1:].strip()

    if current:
        chunks.append(current)

    return chunks


def chunk_file(input_path, output_path):
    print(f"[chunker] lecture : {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    full_text = " ".join(lines)

    print("[chunker] découpage…")
    chunks = chunk_text(full_text)

    print(f"[chunker] écriture : {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(c + "\n\n")

    print("[chunker] terminé.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage : py chunker_book.py input.txt output.txt")
        sys.exit(1)

    inp = sys.argv[1]
    out = sys.argv[2]
    chunk_file(inp, out)
