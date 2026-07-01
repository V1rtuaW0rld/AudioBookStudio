import sys
import re

MIN_CHARS = 250
MAX_CHARS = 400

IDEAL_PUNCT = [";", ".", "!", "?"]
ABBREVIATIONS = ["M.", "Mme.", "Dr.", "etc.", "Mlle.", "St.", "Ste."]
CLOSING_QUOTES = ["»", "”", "\"", "’"]


def is_abbreviation(text, pos):
    """Vérifie si la ponctuation est en fait une abréviation."""
    for abbr in ABBREVIATIONS:
        if text[pos - len(abbr) + 1:pos + 1] == abbr:
            return True
    return False


def find_cut_position(text, min_size, max_size):
    """Trouve la meilleure position de coupure dans text[min:max]."""
    # Niveaux de priorité pour la coupure
    PUNCT_PREFS = [
        [";", ".", "!", "?"],  # Priorité 1 : Fin de phrase
        [":"],                 # Priorité 2 : Deux-points
        [","]                  # Priorité 3 : Virgule
    ]

    for punct_list in PUNCT_PREFS:
        best = -1
        for p in punct_list:
            pos = text.rfind(p, min_size, max_size)
            if pos > best:
                if is_abbreviation(text, pos):
                    continue

                # Vérifie guillemet fermant juste après
                lookahead = text[pos + 1:pos + 4]
                skip = 0
                quote_offset = None

                for ch in lookahead:
                    if ch == " ":
                        skip += 1
                        continue
                    if ch in CLOSING_QUOTES:
                        quote_offset = skip + 1
                    break

                if quote_offset is not None:
                    return pos + quote_offset

                best = pos

        if best != -1:
            return best

    # Si aucune ponctuation valide → on coupe sur le dernier espace dans la plage pour ne pas couper un mot en deux
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
