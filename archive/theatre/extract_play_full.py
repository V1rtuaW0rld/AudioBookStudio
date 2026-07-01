import sys
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

if len(sys.argv) < 3:
    print("Usage: python extract_play_full.py <input.epub> <output.txt>")
    sys.exit(1)

epub_path = sys.argv[1]
output_path = sys.argv[2]
previous_speaker = None


# Liste blanche des personnages
CHARACTERS = [
    "HOEDERER", "HUGO", "OLGA", "JESSICA", "LOUIS",
    "LE PRINCE", "SLICK", "GEORGES", "KARSKY",
    "FRANTZ", "CHARLES", "SPEAKER",
    "VOIX DE HUGO", "VOIX DE CHARLES"
]

def is_scene_title(text):
    return text.strip() == "SCÈNE" or text.strip().startswith("SCÈNE ")

def is_character_name(text):
    if text in CHARACTERS:
        return True
    if any(c in text for c in [",", ".", "(", ")"]):
        return False
    if not all(c.isupper() or c == " " for c in text):
        return False
    return 2 <= len(text) <= 40

def split_character_line(text):
    stripped = text.strip()

    # NOM seul
    if stripped in CHARACTERS:
        return stripped, None

    # NOM + virgule = didascalie
    for name in CHARACTERS:
        if stripped.startswith(name + ","):
            return None, None

    # NOM + réplique
    for name in CHARACTERS:
        prefix = name + " "
        if stripped.startswith(prefix):
            rest = stripped[len(prefix):].strip()
            return name, rest

    # Heuristique
    parts = stripped.split(" ", 1)
    if len(parts) == 2:
        possible_name, rest = parts
        if is_character_name(possible_name):
            return possible_name, rest

    return None, None

def is_didascalie(text):
    stripped = text.strip()

    # Pas un titre de scène
    if is_scene_title(stripped):
        return False

    # Parenthèses courtes
    if stripped.startswith("(") and stripped.endswith(")") and len(stripped) < 40:
        return True

    # NOM + virgule
    for name in CHARACTERS:
        if stripped.startswith(name + ","):
            return True

    # Mots typiques
    keywords = [
        "un temps", "regarde", "geste", "silence",
        "entre", "sort", "sursaute", "regardant",
        "à voix basse", "ironiquement", "doucement",
        "embarrassée", "se met à rire"
    ]
    if any(k in stripped.lower() for k in keywords):
        return True

    # Ligne commençant par minuscule
    if stripped and stripped[0].islower():
        return True

    return False


# EXTRACTION EPUB
book = epub.read_epub(epub_path)
raw_lines = []

for item in book.get_items():
    name = item.get_name().lower()
    if not name.endswith(".xhtml"):
        continue
    if not name.startswith("thea"):
        continue

    soup = BeautifulSoup(item.get_content(), "html.parser")

    for tag in soup.find_all(["h1", "h2", "h3", "p", "i"]):
        text = tag.get_text().strip()
        if not text:
            continue

        # TITRE DE SCÈNE
        if text.replace("\xa0", " ").strip() == "SCÈNE":
            raw_lines.append("SCÈNE")
            continue


        # DIDASCALIE via <i>
        if tag.name == "i":
            # Ne jamais traiter SCÈNE PREMIÈRE / SCÈNE II comme didascalie
            if text.strip().startswith("SCÈNE"):
                continue
            raw_lines.append(f"[DIDASCALIE] {text}")
            continue


        raw_lines.append(text)


# FUSION SCÈNE + numéro (PREMIÈRE, II, III, etc.)
merged = []
i = 0

while i < len(raw_lines):
    raw = raw_lines[i]
    # normalisation agressive : on vire les insécables
    line = raw.replace("\xa0", " ").strip()

    if line == "SCÈNE":
        # Chercher la prochaine ligne non vide
        j = i + 1
        while j < len(raw_lines) and not raw_lines[j].strip():
            j += 1

        if j < len(raw_lines):
            next_line = raw_lines[j].replace("\xa0", " ").strip()

            parts = next_line.split(maxsplit=1)
            numero = parts[0]

            merged.append(f"SCÈNE {numero}")

            if len(parts) > 1:
                merged.append(parts[1])

            i = j + 1
            continue

    merged.append(raw_lines[i])
    i += 1

raw_lines = merged




# NETTOYAGE + STRUCTURATION
output = []
last_line = ""
previous_was_character = False

for line in raw_lines:
    stripped = line.strip()
    if not stripped:
        continue

    # Supprimer doublons exacts ou doublons didascalies
    clean_line = stripped.replace("[DIDASCALIE] ", "")
    clean_last = last_line.replace("[DIDASCALIE] ", "")

    if clean_line == clean_last:
        if stripped.startswith("[DIDASCALIE]") and output:
            output[-1] = stripped
        continue

    last_line = stripped

        # RÈGLE : [DIDASCALIE] ACTEUR, didascalie → doit devenir :
    # ACTEUR
    # [DIDASCALIE] didascalie
    if stripped.startswith("[DIDASCALIE]"):
        content = stripped[len("[DIDASCALIE]"):].strip()

        for name in CHARACTERS:
            prefix = name + ","
            if content.startswith(prefix):
                # Extraire la didascalie sans le nom
                dida = content[len(prefix):].strip()

                # Ajouter le nom du personnage
                output.append(name)
                previous_speaker = name

                # Ajouter la didascalie nettoyée
                output.append(f"[DIDASCALIE] {dida}")

                # Passer à la ligne suivante
                stripped = None
                break

        if stripped is None:
            continue

    # Extraire les didascalies inline (ex: "Oui. (Un temps.) Tu m'as vu ?")
    if "(" in stripped and ")" in stripped:
        import re
        matches = re.findall(r"\([^)]*\)", stripped)
        for m in matches:
            if len(m) < 40:  # didascalie courte
                output.append(f"[DIDASCALIE] {m}")
                stripped = stripped.replace(m, "").strip()
        # RÈGLE : didascalie contenant un ACTEUR → séparer ACTEUR + didascalie
    if stripped.startswith("[DIDASCALIE]"):
        content = stripped[len("[DIDASCALIE]"):].strip()

        for name in CHARACTERS:
            prefix = name + ","
            if content.startswith(prefix):
                dida = content[len(prefix):].strip()

                # On annonce le personnage
                output.append(name)
                previous_speaker = name

                # On ajoute la didascalie propre
                output.append(f"[DIDASCALIE] {dida}")

                # On mémorise que ce personnage est le prochain à parler
                previous_was_character = True

                stripped = None
                break

        if stripped is None:
            continue

    # NOM + réplique
    name, rest = split_character_line(stripped)
    if name:
        output.append(name)
        previous_speaker = name

        previous_was_character = True
        if rest:
            output.append(rest)
            previous_was_character = False
        continue

        # RÈGLE : si la ligne précédente était une didascalie
        # et que la ligne actuelle est une réplique,
        # alors il faut ré‑annoncer le dernier personnage.
        if output and output[-1].startswith("[DIDASCALIE]"):
            # Si ce n'est PAS un nom de personnage
            if not is_character_name(stripped) and not stripped.startswith("[DIDASCALIE]"):
                # On ré‑annonce le dernier personnage connu
                if previous_speaker:
                    output.append(previous_speaker)


    # Ligne après un nom = réplique
    if previous_was_character:
        output.append(stripped)
        previous_was_character = False
        continue

    # Didascalie
    if stripped.startswith("[DIDASCALIE]") or is_didascalie(stripped):
        output.append(f"[DIDASCALIE] {clean_line}")
        continue

    # Texte normal
    output.append(stripped)


# ============================
# PASSE FINALE : DÉDOUBLONNAGE
# ============================

def normalize_dida(line: str) -> str:
    """Normalise une didascalie pour comparaison."""
    s = line.strip()
    if s.startswith("[DIDASCALIE]"):
        s = s[len("[DIDASCALIE]"):].strip()
    return s.lower().replace("  ", " ")

cleaned = []
window = []  # garde les 3 dernières didascalies normalisées

for line in output:
    stripped = line.strip()

    # 1) RÈGLE : [DIDASCALIE] ACTEUR, didascalie → doit devenir :
    # ACTEUR
    # [DIDASCALIE] didascalie
    if stripped.startswith("[DIDASCALIE]"):
        content = stripped[len("[DIDASCALIE]"):].strip()

        for name in CHARACTERS:
            prefix = name + ","
            if content.startswith(prefix):
                dida = content[len(prefix):].strip()

                cleaned.append(name)
                cleaned.append(f"[DIDASCALIE] {dida}")

                window.append(normalize_dida(dida))
                if len(window) > 3:
                    window.pop(0)

                break
        else:
            # pas un cas ACTEUR,
            # on continue le traitement normal des didascalies
            pass
    else:
        # ligne normale → reset fenêtre
        window = []
        cleaned.append(stripped)
        continue

    # 2) Normalisation pour dédoublonnage
    norm = normalize_dida(stripped)

    # 3) Doublon exact consécutif
    if window and norm == window[-1]:
        continue

    # 4) Doublon tronqué (garder la plus longue)
    for prev in cleaned[-3:]:
        if prev.startswith("[DIDASCALIE]"):
            prev_norm = normalize_dida(prev)

            # nouvelle plus courte → doublon tronqué → ignorer
            if norm in prev_norm:
                break

            # nouvelle plus longue → remplacer l’ancienne
            if prev_norm in norm:
                cleaned[-1] = stripped
                break
    else:
        cleaned.append(stripped)
        window.append(norm)
        if len(window) > 3:
            window.pop(0)

output = cleaned


# ============================================
# PASSE FINALE 2 : FUSION SCÈNE + PREMIER MOT
# ============================================

final = []
i = 0

while i < len(output):
    line = output[i].strip()

    # SCÈNE seule sur sa ligne
    if line == "SCÈNE":
        # Chercher la première ligne non vide
        j = i + 1
        while j < len(output) and not output[j].strip():
            j += 1

        if j < len(output):
            next_line = output[j].strip()

            # Premier mot = PREMIÈRE, II, III, etc.
            parts = next_line.split(maxsplit=1)
            numero = parts[0]

            # Fusion
            final.append(f"SCÈNE {numero}")

            # S’il reste du texte après le numéro, on le remet en dessous
            if len(parts) > 1:
                final.append(parts[1])

            # On saute SCÈNE + la ligne suivante
            i = j + 1
            continue

    final.append(output[i])
    i += 1

output = final


# ============================
# ÉCRITURE FINALE
# ============================

with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n\n".join(output))

print("Extraction complète terminée :", output_path)

