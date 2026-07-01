import sys

if len(sys.argv) < 3:
    print("Usage: python clean_names.py <input.txt> <output.txt>")
    sys.exit(1)

input_path = sys.argv[1]
output_path = sys.argv[2]

# Liste blanche des personnages (exactement comme dans le texte)
CHARACTERS = [
    "HOEDERER",
    "HUGO",
    "OLGA",
    "JESSICA",
    "LOUIS",
    "LE PRINCE",
    "SLICK",
    "GEORGES",
    "KARSKY",
    "FRANTZ",
    "CHARLES",
    "SPEAKER",
    "VOIX DE HUGO",
    "VOIX DE CHARLES",
]

def split_character_line(line: str):
    """
    Si la ligne commence par un nom de personnage connu,
    on renvoie (nom, reste) ou (nom, None) si rien derrière.
    Sinon, (None, None).
    """
    stripped = line.strip()
    for name in CHARACTERS:
        if stripped == name:
            return name, None
        prefix = name + " "
        if stripped.startswith(prefix):
            rest = stripped[len(prefix):].strip()
            return name, rest
    return None, None

with open(input_path, "r", encoding="utf-8") as f:
    lines = [l.rstrip("\n") for l in f]

output = []

for line in lines:
    if not line.strip():
        output.append("")  # on garde les lignes vides
        continue

    name, rest = split_character_line(line)

    if name is not None:
        # On a reconnu un personnage
        output.append(name)
        if rest:
            output.append(rest)
    else:
        # Ligne normale
        output.append(line.strip())

with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n".join(output))
