import re
import sys
import os
import json

CHARACTERS = {
    "NAWAL",
    "JEANNE",
    "SIMON",
    "HERMILE LEBEL",
    "ANTOINE",
    "SAWDA",
    "NIHAD",
    "LE MÉDECIN",
    "RALPH",
    "WAHAB",
    "CHAMSEDDINE",
}

def make_actor_regex_pattern(actor: str) -> str:
    esc = re.escape(actor)
    return esc.replace(r"\'", "['’]").replace(r"\’", "['’]").replace("'", "['’]").replace("’", "['’]")

def is_actor(block: str) -> bool:
    block_norm = block.strip().upper().replace("’", "'")
    chars_norm = {c.replace("’", "'") for c in CHARACTERS}
    return block_norm in chars_norm

def is_scene(block: str) -> bool:
    return block.strip().startswith("SCÈNE")

def is_act(block: str) -> bool:
    return block.strip().startswith("ACTE")

def parse_actor_with_didas(block: str):
    block_clean = block.strip()
    for actor in CHARACTERS:
        esc_actor = make_actor_regex_pattern(actor)
        # Case 1: ACTOR (didas) rest
        # e.g., L'Épine (, à Trissotin.) Monsieur...
        m = re.match(rf"^({esc_actor})\s*\(([^)]*)\)\s*(.*)$", block_clean, re.IGNORECASE)
        if m:
            name, dida, rest = m.groups()
            return name.upper().replace("’", "'"), f"({dida}) {rest}"
            
        # Case 2: ACTOR, didas. rest or ACTOR, rest
        # e.g., L'Épine, à Trissotin. Monsieur...
        m = re.match(rf"^({esc_actor})\s*,\s*(.*)$", block_clean, re.IGNORECASE)
        if m:
            name, rest = m.groups()
            return name.upper().replace("’", "'"), rest
            
    return None

def split_parole_with_didas(text: str):
    parts = []
    depth = 0
    start_idx = 0
    
    for i, char in enumerate(text):
        if char == '(':
            if depth == 0:
                if i > start_idx:
                    parts.append(text[start_idx:i])
                start_idx = i
            depth += 1
        elif char == ')':
            if depth > 0:
                depth -= 1
                if depth == 0:
                    parts.append(text[start_idx:i+1])
                    start_idx = i + 1
                    
    if start_idx < len(text):
        parts.append(text[start_idx:])
        
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith("(") and part.endswith(")"):
            yield ("didas", part)
        else:
            yield ("parole", part)

def parse_file(path: str):
    global CHARACTERS
    
    # Try to load custom characters from meta.json in the project root folder
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    meta_path = os.path.join(project_dir, "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
            custom_chars = meta_data.get("custom_characters", [])
            if custom_chars:
                CHARACTERS = {c.strip().upper() for c in custom_chars if c.strip()}
                print(f"[INFO] Personnages personnalisés chargés depuis meta.json : {sorted(list(CHARACTERS))}")
        except Exception as e:
            print(f"[WARN] Impossible de charger les personnages depuis meta.json: {e}")

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    # Normalisation : jamais plus d'une ligne vide
    cleaned = []
    prev_empty = False
    for line in raw.splitlines():
        if line.strip() == "":
            if not prev_empty:
                cleaned.append("")
            prev_empty = True
        else:
            cleaned.append(line)
            prev_empty = False

    raw = "\n".join(cleaned)

    # Découpage en blocs
    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw) if b.strip()]

    output = []
    current_actor = None
    last_kind = "didas"

    for block in blocks:

        # ACTE / SCÈNE
        if is_act(block) or is_scene(block):
            output.append("DIDAS")
            output.append(block)
            current_actor = None
            last_kind = "didas"
            continue

        # ACTEUR + didascalie inline
        parsed = parse_actor_with_didas(block)
        if parsed:
            name, dida = parsed
            current_actor = name
            last_kind = "didas"
            output.append("DIDAS")
            output.append(f"{name}, {dida}")
            continue

        # ACTEUR simple
        if is_actor(block):
            current_actor = block.strip().upper().replace("’", "'")
            last_kind = "didas"
            continue

        # Aucun acteur → DIDAS
        if current_actor is None:
            output.append("DIDAS")
            output.append(block)
            continue

        # Fusion interne des lignes
        merged = " ".join(block.splitlines()).strip()

        # Parenthèses → découpe
        if "(" in merged and ")" in merged:
            for kind, content in split_parole_with_didas(merged):
                if kind == "parole":
                    output.append(current_actor)
                    output.append(content)
                    last_kind = "parole"
                else:
                    output.append("DIDAS")
                    output.append(content)
                    last_kind = "didas"
            continue

        # Alternance parole/didas
        if last_kind == "didas":
            output.append(current_actor)
            output.append(merged)
            last_kind = "parole"
        else:
            output.append("DIDAS")
            output.append(merged)
            last_kind = "didas"

    return output

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage : py parse_theatre.py input.txt output.txt")
        sys.exit(1)

    inp = sys.argv[1]
    out = sys.argv[2]

    parsed = parse_file(inp)

    with open(out, "w", encoding="utf-8") as f:
        for line in parsed:
            f.write(str(line) + "\n")

    print("Fichier généré :", out)
