import sys
import re

MIN_CHARS = 250
MAX_CHARS = 400

# On ne coupe JAMAIS sur ; ni sur espace
IDEAL_PUNCT = [".", "!", "?"]

ABBREVIATIONS = ["M.", "Mme.", "Mlle.", "Dr.", "etc."]
CLOSING_QUOTES = ["»", "”", "\""]


def is_abbreviation(text, pos):
    """Vérifie si la ponctuation est en fait une abréviation."""
    for abbr in ABBREVIATIONS:
        if pos - len(abbr) + 1 >= 0 and text[pos - len(abbr) + 1:pos + 1] == abbr:
            return True
    return False


def chunk_text(text, min_size=MIN_CHARS, max_size=MAX_CHARS):
    chunks = []
    current = text.strip()

    # Niveaux de priorité pour la coupure
    PUNCT_PREFS = [
        [".", "!", "?"],  # Priorité 1 : Fin de phrase
        [":"],            # Priorité 2 : Deux-points
        [","]             # Priorité 3 : Virgule
    ]

    while len(current) > max_size:
        cut_pos = -1

        for punct_list in PUNCT_PREFS:
            for p in punct_list:
                pos = current.rfind(p, min_size, max_size)
                if pos > cut_pos:
                    # 1) Ne pas couper sur abréviation
                    if is_abbreviation(current, pos):
                        continue

                    # 2) Ne pas couper sur initiale (A. B. C. P. etc.)
                    if re.search(r'\b[A-Za-zÀ-ÿ]\.$', current[:pos+1]):
                        continue

                    # 3) Chercher un guillemet fermant juste après
                    lookahead = current[pos+1:pos+4]
                    quote_offset = None
                    skip = 0

                    for ch in lookahead:
                        if ch == " ":
                            skip += 1
                            continue
                        if ch in CLOSING_QUOTES:
                            quote_offset = skip + 1
                        break

                    if quote_offset is not None:
                        cut_pos = pos + quote_offset
                    else:
                        cut_pos = pos
            
            if cut_pos != -1:
                break

        # 5) Si aucune ponctuation valide → on coupe sur le dernier espace dans la plage pour ne pas couper un mot en deux
        if cut_pos == -1:
            pos = current.rfind(" ", min_size, max_size)
            if pos != -1:
                cut_pos = pos
            else:
                # Vraiment aucune coupure propre possible, repli sur max_size
                cut_pos = max_size

        chunk = current[:cut_pos+1].strip()
        chunks.append(chunk)
        current = current[cut_pos+1:].strip()

    if current:
        chunks.append(current)

    return chunks


def chunk_file(input_path, output_path):
    print(f"[chunker] lecture : {input_path}")
    with open(input_path, "r", encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f]

    output = []
    current_actor = None
    buffer = []

    for line in lines:

        # ACTEUR (tout en majuscules, 1 à 3 mots)
        if line.isupper() and 1 <= len(line.split()) <= 3:
            if current_actor and buffer:
                full_text = " ".join(buffer).strip()
                for chunk in chunk_text(full_text):
                    output.append(current_actor)
                    output.append(chunk)
                buffer = []
            current_actor = line
            continue

        # DIDASCALIE
        if line == "DIDAS":
            if current_actor and buffer:
                full_text = " ".join(buffer).strip()
                for chunk in chunk_text(full_text):
                    output.append(current_actor)
                    output.append(chunk)
                buffer = []
            current_actor = "DIDAS"
            continue

        # TEXTE
        if current_actor:
            buffer.append(line)

    # Flush final
    if current_actor and buffer:
        full_text = " ".join(buffer).strip()
        for chunk in chunk_text(full_text):
            output.append(current_actor)
            output.append(chunk)

    print(f"[chunker] écriture : {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        for line in output:
            f.write(line + "\n")

    print("[chunker] terminé.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage : py chunker_theatre.py input_parsed.txt output_chunked.txt")
        sys.exit(1)

    inp = sys.argv[1]
    out = sys.argv[2]
    print(f"[chunker] args : {inp} -> {out}")
    chunk_file(inp, out)
