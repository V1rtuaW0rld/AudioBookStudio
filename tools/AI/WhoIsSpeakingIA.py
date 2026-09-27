# -*- coding: utf-8 -*-
import requests
import sys
import os
import re
import json

# Configuration (surchargée par variables d'env pour l'exécution en Docker :
# depuis le conteneur, LM Studio de l'hôte est joignable via host.docker.internal).
BACKEND = os.environ.get("WHOIS_BACKEND", "lmstudio")

LM_URL = {
    "lmstudio": os.environ.get("LMSTUDIO_URL", "http://localhost:1234/v1/chat/completions"),
    "ollama": os.environ.get("OLLAMA_URL", "http://192.168.0.1:11434/api/chat"),
}[BACKEND]

MODEL = os.environ.get("WHOIS_MODEL", "mistral-small-3.2-24b-instruct-2506")

# --- Phase de préformatage déterministe (aucune IA) ---------------------------
# But : poser automatiquement les accolades que l'on ajoutait à la main.
#   «  -> }«      (ouverture de parole : on ferme la narration)
#   »  -> »{      (fermeture de parole : on rouvre la narration)
#   —  cadratin impair (1er, 3e…) -> —{   (ouvre un aparté narratif)
#   —  cadratin pair  (2e, 4e…)   -> }—   (referme l'aparté, retour parole)
#
# Garde-fou "concierge" : on n'enveloppe PAS un « … » comme parole quand le mot
# juste avant « n'est ni ':' ni un tiret ni un verbe de parole. Dans ce cas la
# citation est grammaticalement intégrée à la phrase du narrateur (discours
# indirect : « en assurant que « ces messieurs » allaient… ») et doit rester
# dans la narration, sans devenir un tour de parole.

_SPEECH_STEMS = (
    "dit", "dis", "disait", "répond", "repond", "repart", "répart", "écria",
    "exclam", "demand", "ajout", "reprit", "reprend", "articul", "murmur",
    "hurl", "lanc", "fit", "poursuiv", "conclu", "soupir", "grond", "aboy",
    "annonc", "ripost", "object", "renchér", "gliss", "coup", "acquiesc",
    "cri", "répét", "repet", "s'écri", "s'exclam",
)


def _mot_avant(texte, idx):
    """Renvoie le dernier mot (ou signe : — – }) avant la position idx."""
    avant = texte[:idx].rstrip()
    if not avant:
        return ""  # début de paragraphe
    if avant[-1] in ":—–}":
        return avant[-1]
    m = re.search(r"(\S+)$", avant)
    return m.group(1) if m else ""


def _est_parole(mot, contenu):
    """True si le contexte annonce une vraie réplique orale (vs citation intégrée).

    contenu = texte entre les guillemets (sans « »)."""
    # 1) contexte d'ouverture franc : ':', tiret, '}', début de paragraphe
    if mot in ("", ":", "—", "–", "}"):
        return True
    # 2) fin de phrase juste avant « -> nouvelle réplique
    if mot and mot[-1] in ".!?…":
        return True
    # 3) contenu commençant par une majuscule -> réplique autonome
    c = contenu.strip()
    if c and c[0].isupper():
        return True
    # 4) verbe de parole juste avant (dit, disait-elle, s'écria…)
    base = mot.strip(".,;!?»«\"()").lower().replace("’", "'")
    tete = base.split("-")[0]          # disait-elle -> disait
    tete = tete.split("'")[-1] if "'" in tete else tete
    return any(base.startswith(s) or tete.startswith(s) for s in _SPEECH_STEMS)


def _wrap_guillemets(texte):
    out, pos = [], 0
    for m in re.finditer(r"«(.*?)»", texte, re.DOTALL):
        out.append(texte[pos:m.start()])
        contenu = m.group(0)
        if _est_parole(_mot_avant(texte, m.start()), m.group(1)):
            out.append("}" + contenu + "{")   # « -> }« ... » -> »{
        else:
            out.append(contenu)               # citation intégrée : reste narration
        pos = m.end()
    out.append(texte[pos:])
    return "".join(out)


def _wrap_cadratins(texte):
    """Balise les tirets cadratins (U+2014).

    - En DÉBUT de ligne  -> tiret de DIALOGUE : '}—' (nouveau tour de parole),
      comme le formatage manuel qui fonctionnait.
    - EN MILIEU de phrase -> vrai APARTÉ/parenthèse : on alterne '—{' (ouvre
      l'aparté narratif) puis '}—' (le referme, retour à la parole).
    """
    out, inline = [], 0
    for i, ch in enumerate(texte):
        if ch == "—":
            j = i - 1
            while j >= 0 and texte[j] in " \t":
                j -= 1
            debut_ligne = (j < 0) or (texte[j] == "\n")
            if debut_ligne:
                out.append("}—")                       # tiret de dialogue
            else:
                inline += 1
                out.append("—{" if inline % 2 == 1 else "}—")  # aparté
        else:
            out.append(ch)
    return "".join(out)


def pretraiter(texte):
    """Préformatage regex avant envoi à l'IA. Idempotent : si le texte semble
    déjà balisé (contient '}«' ou '»{'), on ne touche à rien."""
    if "}«" in texte or "»{" in texte:
        print("[préformatage] texte déjà balisé -> aucune modification.")
        return texte
    return _wrap_cadratins(_wrap_guillemets(texte))


SYSTEM_PROMPT = """Tu es un analyseur littéraire spécialisé dans l’attribution de voix (théâtre / dialogue vs narration).
Ta tâche est de transformer le texte littéraire en pièce de théâtre en attribuant chaque réplique et passage à son locuteur.

Le texte fourni contient des accolades '{' et '}' délimitant certains passages de narration (didascalies / voix du narrateur).
Tu dois :
1. Conserver et compléter ces accolades '{ ... }' pour envelopper TOUS les passages et incises qui relèvent de la narration (ex: "{s'écria-t-il}", "{dit le Directeur}").
2. Tout texte en dehors des accolades '{ ... }' est considéré comme une réplique de personnage. Tu dois précéder chaque réplique par le nom du locuteur en MAJUSCULES sur sa propre ligne (ex: BOULE DE NEIGE, DIRECTEUR, NAPOLÉON).
3. IMPORTANT : Insère TOUJOURS un retour à la ligne avant d'écrire un nom de locuteur en majuscules, sauf s'il suit immédiatement une accolade fermante '}' sur la même ligne (ex: '} BOULE DE NEIGE').
4. Après chaque accolade fermante '}', indique en MAJUSCULES le nom du locuteur qui reprend la parole (ex: '} BOULE DE NEIGE' ou '} NARRATEUR').
5. NE MODIFIE AUCUN MOT du texte d'origine. Ne change pas l'orthographe, ne supprime rien, n'ajoute aucun commentaire.
6. Certaines citations entre guillemets « … » sont laissées SANS accolades car elles sont intégrées à une phrase du narrateur (discours indirect, ex: "en assurant que « ces messieurs » allaient…"). Garde-les DANS la narration : ne crée AUCUN locuteur pour ces citations, ne les enveloppe pas comme une réplique.
7. RÈGLE ABSOLUE — parole vs récit : SEUL le texte entre guillemets « … » ou introduit par un tiret de dialogue est une parole de personnage. TOUT le reste est le NARRATEUR, MÊME à la première personne. Le récit peut être autobiographique : le NARRATEUR dit « je », « j' », « me », « ma mère », etc. (ex: "Je lus la phrase à haute voix", "Je crois qu'il eut ce jour-là la plus grande joie", "j'admirais la toute-puissance paternelle"). Ces phrases de récit ne sont JAMAIS attribuées à un personnage (enfant, etc.) : elles sont NARRATEUR.
"""

FEWSHOT_USER = """{Elle commença par demander à Boule de Neige :}
« Après le soulèvement, y aura-t-il toujours du sucre ?
– Non, lui répondit Boule de Neige d'un ton sec. Nous n'en produisons pas.
– Et pourrai-je porter des rubans ?
– Camarade, repartit Boule de Neige, ces rubans sont l'emblème de ton esclavage. »"""

FEWSHOT_ASSISTANT = """NARRATEUR
{Elle commença par demander à Boule de Neige :}

LUBIE
« Après le soulèvement, y aura-t-il toujours du sucre ?

BOULE DE NEIGE
– Non {lui répondit Boule de Neige d'un ton sec.} BOULE DE NEIGE Nous n'en produisons pas.

LUBIE
– Et pourrai-je porter des rubans ?

BOULE DE NEIGE
– Camarade {repartit Boule de Neige,} BOULE DE NEIGE ces rubans sont l'emblème de ton esclavage. »"""

FEWSHOT2_USER = """– Jamais, lui répondit Filou, d'un ton sans réplique. Dans ce chenil, nous n'avons pas de place pour un chien de plus. De toute façon, la meute est déjà complète."""

FEWSHOT2_ASSISTANT = """FILOU
– Jamais {lui répondit Filou, d'un ton sans réplique.} FILOU Dans ce chenil, nous n'avons pas de place pour un chien de plus. De toute façon, la meute est déjà complète."""


def analyse_ia_seule(texte):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": FEWSHOT_USER},
        {"role": "assistant", "content": FEWSHOT_ASSISTANT},
        {"role": "user", "content": FEWSHOT2_USER},
        {"role": "assistant", "content": FEWSHOT2_ASSISTANT},
        {"role": "user", "content": texte},
    ]

    if BACKEND == "ollama":
        payload = {
            "model": MODEL,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 4096},
        }
        r = requests.post(LM_URL, json=payload, timeout=600)
        r.raise_for_status()
        content = r.json()["message"]["content"]
    else:
        payload = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 4096,
        }
        r = requests.post(LM_URL, json=payload, timeout=600)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]

    return content


import difflib


# --- Découpage narration/parole : 100% DÉTERMINISTE ---------------------------
# On n'utilise PAS l'IA pour décider ce qui est narration vs parole. On se fie
# uniquement aux marqueurs posés par pretraiter() dans le texte préformaté :
#   '}' = début d'une PAROLE (nouveau tour de personnage)
#   '{' = début de NARRATION
# État initial = narration. Chaque '}' ouvre un NOUVEAU segment de parole (donc
# deux répliques consécutives = deux segments distincts). Conséquence garantie :
# tout le récit hors guillemets/tirets (y compris le « je » narratif de Marcel)
# tombe forcément en NARRATEUR, quoi que « pense » le modèle.

def segmenter_preforme(texte):
    segments, etat, buf = [], "N", []

    def flush(courant):
        txt = "".join(buf).strip()
        if txt:
            segments.append({"type": courant, "text": txt})
        buf.clear()

    for ch in texte:
        if ch == "}":
            flush(etat)
            etat = "S"
        elif ch == "{":
            flush(etat)
            etat = "N"
        else:
            buf.append(ch)
    flush(etat)
    return segments


def _mots(t):
    t = re.sub(r"[«»\"{}()\[\]…—–\-.,;:!?’']", " ", t)
    return [w.lower() for w in t.split() if w]


def _blocs_ia(reponse):
    """Extrait de la réponse IA la liste ordonnée (locuteur, texte) par bloc."""
    blocs, speaker, buf = [], None, []
    for ligne in reponse.split("\n"):
        l = ligne.strip()
        if not l:
            continue
        if l.isupper() and len(l) < 50 and not any(c in l for c in ".!?«»\"–—{}"):
            if speaker is not None and buf:
                blocs.append((speaker, " ".join(buf)))
            speaker, buf = l, []
        elif speaker is not None:
            buf.append(l)
    if speaker is not None and buf:
        blocs.append((speaker, " ".join(buf)))
    return blocs


def assigner_locuteurs(segments, reponse):
    """Narration -> NARRATEUR (déterministe). Parole -> nom trouvé par
    l'IA, associé au segment par meilleure correspondance de texte (pas par
    ordre aveugle), avec repli 'PERSONNAGE' si aucune correspondance fiable."""
    perso = [(s, _mots(t)) for s, t in _blocs_ia(reponse) if s != "NARRATEUR"]
    paires = []
    for seg in segments:
        if seg["type"] == "N":
            paires.append(("NARRATEUR", seg["text"]))
            continue
        cible = _mots(seg["text"])
        best, best_r = "PERSONNAGE", 0.0
        for nom, mots in perso:
            r = difflib.SequenceMatcher(None, cible, mots).ratio()
            if r > best_r:
                best_r, best = r, nom
        paires.append((best if best_r >= 0.30 else "PERSONNAGE", seg["text"]))
    return paires


def _nettoyer(t):
    t = t.replace("{", " ").replace("}", " ")
    t = re.sub(r"[«»\"]", "", t)
    t = re.sub(r"^\s*[—–]\s*", "", t)      # tiret de dialogue en tête de réplique
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def rendu_final_txt(paires):
    fusion = []
    for loc, txt in paires:
        txt = _nettoyer(txt)
        if not txt:
            continue
        if fusion and fusion[-1][0] == loc:
            fusion[-1] = (loc, fusion[-1][1] + " " + txt)
        else:
            fusion.append((loc, txt))
    lignes = []
    for loc, txt in fusion:
        lignes.append(f"\n{loc}\n")
        lignes.append(txt)
    return "\n".join(lignes).strip()


def formater_sortie_ia(reponse_ia, texte_pretraite):
    segments = segmenter_preforme(texte_pretraite)
    paires = assigner_locuteurs(segments, reponse_ia)
    return rendu_final_txt(paires)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python WhoIsSpeakingIA.py fichier.txt")
        sys.exit(1)
        
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        texte = f.read()

    print("Préformatage (regex, sans IA)...")
    texte_pretraite = pretraiter(texte)

    print("Appel de l'IA (Mistral-Small 24B)...")
    reponse_brute = analyse_ia_seule(texte_pretraite)
    
    print("\n--- RÉPONSE BRUTE DE L'IA ---")
    print(reponse_brute)

    print("\n--- DÉCOUPAGE DÉTERMINISTE & RENDU ---")
    rendu_final = formater_sortie_ia(reponse_brute, texte_pretraite)
    print(rendu_final)

    # Vérification d'intégrité : aucun mot du texte d'origine ne doit disparaître.
    def normaliser_mots(t):
        cleaned = re.sub(r"[\{\}\(\)«»\"’'–—\-.,!?;:]", " ", t)
        noms = {"narrateur", "personnage", "père", "pere", "enfant", "mère", "mere",
                "poule", "napoléon", "animal", "boule", "neige", "lubie",
                "directeur", "filou", "augustine", "tante", "marie", "concierge"}
        return [w.lower() for w in cleaned.split() if w.lower() not in noms]

    orig_words = normaliser_mots(texte)
    final_words = normaliser_mots(rendu_final)
    
    sm = difflib.SequenceMatcher(None, orig_words, final_words)
    ratio = sm.ratio()
    print(f"\n[VÉRIFICATION DE SÉCURITÉ] Mots conservés : {ratio*100:.1f}%")
    if ratio < 0.995:
        print("[ATTENTION] L'IA a peut-être oublié ou altéré des mots lors de la génération.")
    else:
        print("[OK] Tous les mots d'origine semblent présents dans le résultat.")
        
    # Enregistrement à côté du fichier d'entrée : <nom_entrée>_ai.txt
    chemin_entree = os.path.abspath(sys.argv[1])
    dossier = os.path.dirname(chemin_entree) or "."
    base = os.path.splitext(os.path.basename(chemin_entree))[0]
    chemin_sortie = os.path.join(dossier, f"{base}_ai.txt")

    with open(chemin_sortie, "w", encoding="utf-8") as f:
        f.write(rendu_final + "\n")
    print(f"\n[OK] Résultat enregistré dans : {chemin_sortie}")
