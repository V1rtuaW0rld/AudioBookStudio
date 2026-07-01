import re
import sys

CHARACTERS = {
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
    "NARRATOR",
    "IVAN",
}

def is_actor(block: str) -> bool:
    return block.strip() in CHARACTERS

def is_scene(block: str) -> bool:
    return block.strip().startswith("SCÈNE")

def is_act(block: str) -> bool:
    return block.strip().startswith("ACTE")

def parse_actor_with_didas(block: str):
    """
    Cas n°3 : 'OLGA, sans poser le revolver.'
    -> ('OLGA', 'sans poser le revolver.')
    """
    if "," not in block:
        return None
    name, rest = block.split(",", 1)
    name = name.strip()
    if name in CHARACTERS:
        return name, rest.strip()
    return None

def split_parole_with_didas(text: str):
    """
    Cas n°2 : HUGO + texte avec parenthèses
    'Oui. (Un temps.) Tu m'as bien vu ? (Didas...) Alors...'
    -> alternance ('parole', 'didas') dans l'ordre.
    """
    parts = re.split(r"(\([^)]*\))", text)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith("(") and part.endswith(")"):
            yield ("didas", part)
        else:
            yield ("parole", part)

def parse_file(path: str):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    # Un bloc = séparé par au moins une ligne vide
    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw) if b.strip()]

    output = []
    current_actor = None
    last_kind_for_actor = "didas"  # pour l'alternance locale (parole/didas)

    for block in blocks:

        # --- ACTE / SCÈNE -> DIDAS, reset acteur ---
        if is_act(block) or is_scene(block):
            output.append("DIDAS")
            output.append(block)
            current_actor = None
            last_kind_for_actor = "didas"
            continue

        # --- ACTEUR + didascalie inline (cas n°3) ---
        parsed = parse_actor_with_didas(block)
        if parsed:
            name, dida = parsed
            current_actor = name
            last_kind_for_actor = "didas"
        
            # DIDAS complète : "Olga, sans poser le revolver."
            didas_full = f"{name.capitalize()}, {dida}"
        
            output.append("DIDAS")
            output.append(didas_full)
            continue


        # --- ACTEUR simple ---
        if is_actor(block):
            current_actor = block.strip()
            last_kind_for_actor = "didas"  # prochain bloc = parole
            continue  # on n'écrit pas le nom ici

        # --- Aucun acteur courant -> DIDAS (cas n°1 début de pièce/scène) ---
        if current_actor is None:
            output.append("DIDAS")
            output.append(block)
            continue

        # --- Bloc avec parenthèses -> cas n°2 : on découpe à l'intérieur ---
        if "(" in block and ")" in block:
            for kind, content in split_parole_with_didas(block):
                if kind == "parole":
                    output.append(current_actor)
                    output.append(content)
                    last_kind_for_actor = "parole"
                else:
                    output.append("DIDAS")
                    output.append(content)
                    last_kind_for_actor = "didas"
            continue

        # --- Alternance locale PAROLE / DIDAS pour cet acteur ---
        if last_kind_for_actor == "didas":
            # on passe à une parole
            output.append(current_actor)
            output.append(block)
            last_kind_for_actor = "parole"
        else:
            # on passe à une didascalie
            output.append("DIDAS")
            output.append(block)
            last_kind_for_actor = "didas"

    return output


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage : py parse_theatre.py input.txt output.txt")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    parsed = parse_file(input_path)

    with open(output_path, "w", encoding="utf-8") as f:
        for line in parsed:
            f.write(line + "\n")

    print("Fichier généré :", output_path)
