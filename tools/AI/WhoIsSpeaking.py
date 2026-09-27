import requests
import sys
import os
import re
import json
import difflib

# --- Serveur local (aucune API payante) -----------------------------------
# Un seul interrupteur pour basculer entre les deux backends : ils n'ont pas
# le même format de requête/réponse (voir analyse()).
#   "lmstudio" -> http://localhost:1234/v1/chat/completions (API compatible OpenAI)
#   "ollama"   -> http://localhost:11434/api/chat           (API native, permet num_ctx)
BACKEND = "lmstudio"

LM_URL = {
    "lmstudio": "http://localhost:1234/v1/chat/completions",
    "ollama": "http://192.168.0.1:11434/api/chat",  # ajuste l'IP/port si besoin
}[BACKEND]
NUM_CTX = 8192  # marge large (utilisé seulement côté Ollama) : prompt système
                # + 3 exemples + texte à analyser

# Recommandé sur RTX 4070 Ti Super (16 Go) : un 14B-32B, bien plus fiable que le 8B.
# ATTENTION VRAM : Qwen2.5-32B en Q4_K_M pèse ~19-20 Go, ça DÉPASSE les 16 Go de
# VRAM -> offload CPU partiel -> plus LENT, pas plus rapide. Pour rester dans les
# 16 Go, préférer un quant plus léger (Q3_K_M ~15 Go, IQ3_XS/IQ4_XS ~14-17 Go).
#   Qwen2.5-14B-Instruct (Q5_K_M)        -> correct, mais moins fiable que le 24B
#   Mistral-Small-3.2-24B-Instruct (Q4)  -> meilleur résultat obtenu jusqu'ici (LM Studio)
#   Qwen2.5-32B-Instruct (Q3_K_M/IQ4_XS) -> pas meilleur ici, tient de justesse en 16 Go
#   Qwen2.5-32B-Instruct (Q4_K_M)        -> ~19-20 Go, ne tient PAS en 16 Go
#MODEL = "qwen2.5-14b-instruct"
MODEL = "mistral-small-3.2-24b-instruct-2506"
#MODEL = "Sub01/Qwen2.5-32B-Instruct:q4_k_m"
#MODEL = "vanilj/qwen2.5-32b-instruct_iq4_xs"

SYSTEM_PROMPT = """Tu es un analyseur littéraire spécialisé dans l’attribution de voix (théâtre / dialogue vs narration).
Ta tâche est de restructurer le texte sous forme de pièce de théâtre en faisant deux choses :
1. Attribue chaque paragraphe ou réplique au personnage qui parle (ex: DIRECTEUR, BOULE DE NEIGE) ou à NARRATEUR.
2. Pour les dialogues des personnages, isole les incises narratives (ex: "dit-il", "ajouta-t-elle", "s'écria-t-il") ainsi que les descriptions d'actions narratives immédiates en les mettant STRICTEMENT entre parenthèses ( ... ).
3. IMPORTANT : Immédiatement après chaque parenthèse fermante ')', indique en MAJUSCULES le nom du locuteur qui reprend la parole (ex: ') DIRECTEUR' si le personnage continue de parler, ou ') NARRATEUR' si la narration se poursuit).

Règles impératives :
1. NE MODIFIE AUCUN MOT du texte d'origine. Ne change pas l'orthographe, ne supprime rien, n'ajoute aucun commentaire.
2. Écris le nom du locuteur en MAJUSCULES sur sa propre ligne, suivi du texte.
3. Si un paragraphe complet est narratif, attribue-le simplement à NARRATEUR.
"""

SYSTEM_PROMPT_PASSE2 = """Voici un texte original et sa segmentation préliminaire sous forme de texte théâtral avec parenthèses.
Ta tâche est de corriger le balisage :
1. Assure-toi que toutes les incises de dialogue (ex: "dit-il", "ajoutait-il") et les incises d'actions courtes sont bien entourées de parenthèses ( ... ).
2. Assure-toi que chaque parenthèse fermante ')' est immédiatement suivie du nom du locuteur reprenant le cours de la phrase (ex: ') PERSONNAGE' ou ') NARRATEUR').
3. NE MODIFIE AUCUN MOT, NE SUPPRIME RIEN.

Renvoie le résultat complet corrigé sous le même format théâtral.
"""

# Exemple few-shot : montre les 2 cas durs (incise + alternance des tirets).
FEWSHOT_USER = """(Elle commença par demander à Boule de Neige :)
« Après le soulèvement, y aura-t-il toujours du sucre ?
– Non (lui répondit Boule de Neige d'un ton sec.) Nous n'en produisons pas.
– Et pourrai-je porter des rubans ?
– Camarade (repartit Boule de Neige,) ces rubans sont l'emblème de ton esclavage. »"""

FEWSHOT_ASSISTANT = """NARRATEUR
(Elle commença par demander à Boule de Neige :)

LUBIE
« Après le soulèvement, y aura-t-il toujours du sucre ?

BOULE DE NEIGE
– Non (lui répondit Boule de Neige d'un ton sec.) BOULE DE NEIGE Nous n'en produisons pas.

LUBIE
– Et pourrai-je porter des rubans ?

BOULE DE NEIGE
– Camarade (repartit Boule de Neige,) BOULE DE NEIGE ces rubans sont l'emblème de ton esclavage. »"""

# 2e exemple (personnages DIFFÉRENTS du texte réel, pour éviter toute
# interférence/confusion) : incise SANS tiret, noyée en plein milieu d'une
# longue réplique, + un connecteur de discours qui ne doit PAS être coupé.
FEWSHOT2_USER = """– Jamais (lui répondit Filou, d'un ton sans réplique.) Dans ce chenil, nous n'avons pas de place pour un chien de plus. De toute façon, la meute est déjà complète."""

FEWSHOT2_ASSISTANT = """FILOU
– Jamais (lui répondit Filou, d'un ton sans réplique.) FILOU Dans ce chenil, nous n'avons pas de place pour un chien de plus. De toute façon, la meute est déjà complète."""

FEWSHOT3_USER = """(Grondin bondit sur le muret et aboya :)
– Silence dans les rangs ! On repart dans cinq minutes."""

FEWSHOT3_ASSISTANT = """NARRATEUR
(Grondin bondit sur le muret et aboya :)

GRONDIN
– Silence dans les rangs ! On repart dans cinq minutes."""


def aligner_didascalies(texte_original, response_tagged):
    """Reconstitue les segments d'origine en mappant les tags de l'IA sur le texte original exact."""
    lignes = response_tagged.split('\n')
    response_words = []
    locuteur_bloc = "NARRATEUR"
    dans_parenthese = False
    
    for ligne in lignes:
        l = ligne.strip()
        if not l:
            continue
            
        if l.isupper() and len(l) < 50 and not any(c in l for c in ".!?«»\"–—()"):
            locuteur_bloc = l
            dans_parenthese = False
            continue
            
        parts = re.split(r"([\(\)])", l)
        i = 0
        while i < len(parts):
            part = parts[i]
            if not part:
                i += 1
                continue
                
            if part == '(':
                dans_parenthese = True
            elif part == ')':
                dans_parenthese = False
                # Regarde si le mot suivant commence par un nom de locuteur en majuscules
                if i + 1 < len(parts):
                    suivante = parts[i + 1].strip()
                    m = re.match(r"^([A-ZÀ-Ý0-9_\s\-]{3,50})\b", suivante)
                    if m:
                        nouveau_locuteur = m.group(1).strip()
                        locuteur_bloc = nouveau_locuteur
                        parts[i + 1] = suivante[len(nouveau_locuteur):].strip()
            else:
                mots = part.split()
                locuteur_courant = "NARRATEUR" if dans_parenthese else locuteur_bloc
                for w in mots:
                    response_words.append((w, locuteur_courant))
            i += 1

    original_words = texte_original.split()
    if not original_words:
        return []
        
    def normaliser(w):
        w_norm = re.sub(r"[^\wÀ-ÿ]", "", w).lower()
        return w_norm
        
    orig_norm = [normaliser(w) for w in original_words]
    resp_norm = [normaliser(w[0]) for w in response_words]
    
    sm = difflib.SequenceMatcher(None, orig_norm, resp_norm, autojunk=False)
    original_locuteurs = [None] * len(original_words)
    
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for idx in range(i2 - i1):
                original_locuteurs[i1 + idx] = response_words[j1 + idx][1]
        elif tag == "replace":
            min_len = min(i2 - i1, j2 - j1)
            for idx in range(min_len):
                original_locuteurs[i1 + idx] = response_words[j1 + idx][1]
                
    dernier_locuteur = "NARRATEUR"
    for idx in range(len(original_words)):
        if original_locuteurs[idx] is None:
            original_locuteurs[idx] = dernier_locuteur
        else:
            dernier_locuteur = original_locuteurs[idx]
            
    segments = []
    locuteur_courant = original_locuteurs[0]
    mots_segment = [original_words[0]]
    
    for idx in range(1, len(original_words)):
        loc = original_locuteurs[idx]
        if loc == locuteur_courant:
            mots_segment.append(original_words[idx])
        else:
            segments.append({"locuteur": locuteur_courant, "texte": " ".join(mots_segment)})
            locuteur_courant = loc
            mots_segment = [original_words[idx]]
            
    if mots_segment:
        segments.append({"locuteur": locuteur_courant, "texte": " ".join(mots_segment)})
        
    return segments

def analyse(texte, feedback=None):
    user_content = pre_baliser(texte)
    if feedback:
        manquants = feedback.get("manquants", [])
        erreurs_structure = feedback.get("erreurs_structure", [])
        
        user_content += "\n\n[ATTENTION - ESSAI PRÉCÉDENT À CORRIGER]\nLors de la tentative précédente, tu as fait des erreurs que tu dois ABSOLUMENT corriger pour cet essai :\n"
        
        if manquants:
            user_content += "\n1. PASSAGES OUBLIÉS OU ALTÉRÉS (NE PERDS AUCUN MOT) :\n"
            for passage, contexte in manquants:
                user_content += f"- « {passage} » (à réinsérer près de la zone contenant : ...{contexte}...)\n"
                
        if erreurs_structure:
            user_content += "\n2. ERREURS DE STRUCTURE (SÉPARATION DIALOGUE/NARRATEUR) :\n"
            for txt, msg in erreurs_structure:
                user_content += f"- {msg} (Concerne le passage : « {txt[:50]}... »)\n"
                
        user_content += "\nRecommence la segmentation complète de tout le texte sans rien oublier ni modifier."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": FEWSHOT_USER},
        {"role": "assistant", "content": FEWSHOT_ASSISTANT},
        {"role": "user", "content": FEWSHOT2_USER},
        {"role": "assistant", "content": FEWSHOT2_ASSISTANT},
        {"role": "user", "content": FEWSHOT3_USER},
        {"role": "assistant", "content": FEWSHOT3_ASSISTANT},
        {"role": "user", "content": user_content},
    ]

    if BACKEND == "ollama":
        payload = {
            "model": MODEL,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.0, "num_ctx": NUM_CTX, "num_predict": 4096},
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


def analyse_passe2(texte_original, content_p1):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_PASSE2},
        {"role": "user", "content": f"Texte original :\n{texte_original}\n\nSegmentation préliminaire :\n{content_p1}"},
    ]

    if BACKEND == "ollama":
        payload = {
            "model": MODEL,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.0, "num_ctx": NUM_CTX, "num_predict": 4096},
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


# --- Post-traitement déterministe (ce que le modèle rate systématiquement) --
# Pour un TTS, les guillemets « » ne servent à rien (ils ne se lisent pas et
# créent parfois une pause/hoquet) : on les retire. Et surtout, on redécoupe
# les INCISES noyées (« … aux foins ! s'écria Boule de Neige. Il y va… ») que
# le modèle laisse collées à la réplique : verbe de parole + sujet -> NARRATEUR,
# le reste reste au personnage. Regex mise au point sur banc de test.
# Note : les apostrophes sont rendues agnostiques (droite ' OU typographique ’)
# via .replace() plus bas — sinon « s’écria » du texte réel n'est pas reconnu.
VERBES_LIST = [
    "dit", "répondit", "repartit", "s'écria", "s'exclama", "demanda", "ajouta", "reprit", "articula",
    "murmura", "hurla", "lança", "fit", "poursuivit", "conclut", "soupira", "gronda", "s'écriait",
    "aboya", "annonça", "riposta", "objecta", "renchérit", "glissa", "coupa", "acquiesça",
    "disait", "répondait", "ajoutait", "reprenait", "murmurait", "hurlait", "lançait", "faisait"
]
VERBES = "|".join(rf"[{v[0].lower()}{v[0].upper()}]{v[1:]}" for v in VERBES_LIST).replace("'", "['’]")
_PRONOM_OBJ = r"(?:lui|leur|me|te|se|nous|vous|y)\s+"
_MANIERE = r",?\s*(?:d['’]|avec\b|sur\b)[^.!?»«\"]*"
_SUJET = r"(?:\s+(?:le|la|l['’]|les|un|une|des|ce|cet|cette|mon|ton|son|notre|votre|leur|de|du)?\s*[A-ZÀ-Ý][\wÀ-ÿ'’\-\.]*(?:\s+(?:de|du|des|d['’])?\s*[A-ZÀ-Ý][\wÀ-ÿ'’\-\.]*)*|-\w+)"

INCISE_RE = re.compile(
    rf"\s*(?:{_PRONOM_OBJ})?(?:{VERBES})\b{_SUJET}(?:{_MANIERE})?\s*[.,]?"
)

def splitter_dialogues(texte):
    lignes = texte.split('\n')
    nouvelles_lignes = []
    for l in lignes:
        l_strip = l.strip()
        if not l_strip:
            nouvelles_lignes.append("")
            continue
        parts = re.split(r"(»|”)", l_strip)
        sub_lignes = []
        temp = ""
        for p in parts:
            if p in ("»", "”"):
                temp += p
                sub_lignes.append(temp.strip())
                temp = ""
            else:
                temp += p
        if temp.strip():
            sub_lignes.append(temp.strip())
            
        sub_lignes2 = []
        for sl in sub_lignes:
            parts2 = re.split(r"(?<=.)(?=[«“—–])", sl)
            for p2 in parts2:
                if p2.strip():
                    sub_lignes2.append(p2.strip())
        nouvelles_lignes.extend(sub_lignes2)
    return "\n".join(nouvelles_lignes)

def reconstruire_paragraphes(texte):
    texte_splitte = splitter_dialogues(texte)
    lignes = texte_splitte.split('\n')
    paragraphes = []
    para_courant = []
    dernier_est_dialogue = False
    
    for l in lignes:
        l_strip = l.strip()
        if not l_strip:
            if para_courant:
                paragraphes.append(" ".join(para_courant))
                para_courant = []
            continue
            
        est_dialogue = l_strip.startswith(("—", "–", "-", "«", "“", '"'))
        
        if para_courant and est_dialogue != dernier_est_dialogue:
            paragraphes.append(" ".join(para_courant))
            para_courant = [l_strip]
        elif est_dialogue:
            if para_courant:
                paragraphes.append(" ".join(para_courant))
            para_courant = [l_strip]
        else:
            para_courant.append(l_strip)
            
        dernier_est_dialogue = est_dialogue
        
    if para_courant:
        paragraphes.append(" ".join(para_courant))
    return paragraphes

def pre_baliser(texte):
    paragraphes = reconstruire_paragraphes(texte)
    out = []
    for p in paragraphes:
        p_strip = p.strip()
        if not p_strip:
            continue
            
        contient_dialogue = any(c in p_strip for c in "—–-«»\"“")
        if not contient_dialogue:
            out.append(f"({p_strip})")
        else:
            p_mod = INCISE_RE.sub(lambda m: f" ({m.group(0).strip()}) ", p_strip)
            p_mod = re.sub(r"\s+", " ", p_mod).strip()
            # Nettoyage des ponctuations bizarres
            p_mod = re.sub(r",\s*\(", " (", p_mod)
            p_mod = re.sub(r"\)\s*,", ")", p_mod)
            out.append(p_mod)
    return "\n\n".join(out)

def nettoyer_guillemets(texte):
    return re.sub(r"[«»\"]", "", texte).strip()


def decouper_incises(locuteur, texte):
    """Découpe un segment personnage contenant une ou plusieurs incises noyées :
    les incises passent NARRATEUR, le reste garde le locuteur d'origine."""
    if locuteur == "NARRATEUR":
        return [(locuteur, texte)]
    out, pos = [], 0
    for m in INCISE_RE.finditer(texte):
        avant = texte[pos:m.start()].strip()
        incise = m.group(0).strip()
        if avant:
            out.append((locuteur, avant))
        out.append(("NARRATEUR", incise))
        pos = m.end()
    reste = texte[pos:].strip()
    if reste:
        out.append((locuteur, reste))
    return out or [(locuteur, texte)]


def post_traitement(segments):
    # 1. Redécoupe les incises noyées dans chaque segment de personnage.
    etendu = []
    for seg in segments:
        for loc, frag in decouper_incises(seg.get("locuteur", ""), seg.get("texte", "")):
            etendu.append({"locuteur": loc, "texte": frag})

    # 2. Règle stricte : si un bloc attribué à un personnage ne contient ni guillemets
    #    ni tiret de parole, et qu'il fait plus d'une dizaine de mots, c'est de la narration pure
    #    (ex: les grands blocs réflexifs). On le reclasse en NARRATEUR.
    corrige_blocs = []
    for seg in etendu:
        loc, txt = seg["locuteur"], seg["texte"].strip()
        if loc != "NARRATEUR":
            # Si le fragment ne commence/contient aucun marqueur de dialogue et est un peu long
            contient_marqueur = any(c in txt for c in "—–-«»\"“") or txt.startswith(("«", "“", '"', "—", "–", "-"))
            if not contient_marqueur and len(txt.split()) > 6:
                loc = "NARRATEUR"
        corrige_blocs.append({"locuteur": loc, "texte": txt})

    # 3. Cas où le modèle a mis l'incise ET la suite de réplique dans un segment
    #    NARRATEUR : on rend la queue au personnage qui parlait juste avant.
    corrige = []
    for seg in corrige_blocs:
        loc, txt = seg["locuteur"], seg["texte"]
        if loc == "NARRATEUR" and corrige and corrige[-1]["locuteur"] != "NARRATEUR":
            m = INCISE_RE.match(txt)
            if m and txt[m.end():].strip():
                corrige.append({"locuteur": "NARRATEUR", "texte": txt[:m.end()].strip()})
                corrige.append({"locuteur": corrige[-2]["locuteur"], "texte": txt[m.end():].strip()})
                continue
        corrige.append({"locuteur": loc, "texte": txt})

    # 4. Retire les guillemets, jette les fragments vides, fusionne les segments
    #    consécutifs du même locuteur.
    resultat = []
    for seg in corrige:
        texte_propre = nettoyer_guillemets(seg["texte"])
        # Retire d'éventuelles parenthèses résiduelles collées aux mots
        texte_propre = re.sub(r"[\(\)]", "", texte_propre).strip()
        if not texte_propre:
            continue
        if resultat and resultat[-1]["locuteur"] == seg["locuteur"]:
            resultat[-1]["texte"] += " " + texte_propre
        else:
            resultat.append({"locuteur": seg["locuteur"], "texte": texte_propre})
    return resultat


def _normaliser_pour_comparaison(texte):
    """Enlève guillemets/ponctuation de citation et aplatit les espaces, pour
    comparer le FOND (les mots) sans se faire piéger par « », tirets, etc."""
    t = re.sub(r"[«»\"‘’–—–—]", " ", texte)
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def verifier_integrite(texte_original, segments):
    """Vérification déterministe (AUCUNE IA) : le texte reconstitué à partir
    des segments doit contenir tous les mots du texte d'origine. Repère les
    mots/phrases que le modèle aurait supprimés, dupliqués ou reformulés.
    Retourne (taux_de_similarite, liste_des_passages_manquants)."""
    original = _normaliser_pour_comparaison(texte_original).split()
    reconstitue = _normaliser_pour_comparaison(" ".join(s["texte"] for s in segments)).split()

    sm = difflib.SequenceMatcher(None, original, reconstitue, autojunk=False)
    manquants = []
    for tag, i1, i2, j1, _j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            passage = " ".join(original[i1:i2])
            # Ignore le bruit : différences purement ponctuation/espaces (ex.
            # « ! , » issu des guillemets retirés) — pas une perte de contenu.
            if not re.search(r"[\wÀ-ÿ]", passage):
                continue
            # Contexte = les mots reconstitués juste avant le trou, pour
            # localiser où réinsérer le passage manquant en un coup d'œil.
            contexte = " ".join(reconstitue[max(0, j1 - 6):j1])
            manquants.append((passage, contexte))
    return sm.ratio(), manquants


def verifier_structure(segments):
    """Linter de structure (AUCUNE IA) : repère les incohérences manifestes de la segmentation."""
    erreurs = []
    for seg in segments:
        loc = seg.get("locuteur", "")
        txt = seg.get("texte", "").strip()
        # Si un segment NARRATEUR commence clairement par un tiret ou un guillemet ouvrant
        if loc == "NARRATEUR" and txt.startswith(("—", "–", "-", "«", "“", '"')):
            if len(txt) > 2:
                msg = "Tu as attribué au NARRATEUR un segment qui commence par une marque de dialogue. Tu dois absolument séparer la réplique (qui appartient au personnage) de l'incise éventuelle (NARRATEUR)."
                erreurs.append((txt, msg))
    return erreurs


def prochain_fichier_sortie(dossier):
    """sortie1.txt, sortie2.txt, ... — ne réécrase jamais un essai précédent."""
    n = 1
    while True:
        chemin = os.path.join(dossier, f"sortie{n}.txt")
        if not os.path.exists(chemin):
            return chemin
        n += 1


def rendu(segments):
    """Rendu lisible : en-tête de locuteur quand il change, puis le texte."""
    lignes = []
    dernier = None
    for s in segments:
        loc = s.get("locuteur", "?")
        txt = s.get("texte", "")
        if loc != dernier:
            lignes.append(f"\n{loc}\n")
            dernier = loc
        lignes.append(txt)
    return "\n".join(lignes).strip()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python WhoIsSpeaking.py fichier.txt")
        sys.exit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        texte = f.read()

    max_essais = 3
    meilleur_ratio = 0
    meilleurs_segments = []
    feedback = None

    for essai in range(1, max_essais + 1):
        print(f"\n--- ESSAI {essai}/{max_essais} ---")
        print("Passe 1 : Proposition initiale par LLM...")
        try:
            segments_p1 = analyse(texte, feedback)
        except Exception as e:
            print(f"Erreur LLM (Passe 1) : {e}")
            continue
            
        print("Passe 2 : Correction par LLM...")
        try:
            segments_p2 = analyse_passe2(texte, segments_p1)
        except Exception as e:
            print(f"Erreur LLM (Passe 2) : {e}")
            segments_p2 = segments_p1

        print("Post-traitement déterministe...")
        segments_finaux = aligner_didascalies(texte, segments_p2)
        segments_finaux = post_traitement(segments_finaux)
        
        ratio, manquants = verifier_integrite(texte, segments_finaux)
        erreurs_structure = verifier_structure(segments_finaux)
        print(f"Vérification intégrité : {ratio*100:.1f}%, Erreurs structurelles : {len(erreurs_structure)}")
        
        if ratio > meilleur_ratio or (ratio == meilleur_ratio and not erreurs_structure):
            meilleur_ratio = ratio
            meilleurs_segments = segments_finaux
            
        if not manquants and not erreurs_structure:
            print("[OK] Intégrité de 100% et structure parfaites, on arrête les essais.")
            break
        else:
            print(f"[ATTENTION] Intégrité ou structure insuffisante, on relance avec feedback.")
            feedback = {"manquants": manquants, "erreurs_structure": erreurs_structure}

    texte_rendu = rendu(meilleurs_segments)
    print("\n--- RÉSULTAT FINAL ---")
    print(texte_rendu)

    print(f"\n[VÉRIFICATION FINALE] Fidélité au texte source : {meilleur_ratio*100:.1f}%")
    _, manquants = verifier_integrite(texte, meilleurs_segments)
    if manquants:
        print("[ATTENTION] Passages manquants ou altérés :")
        for passage, contexte in manquants:
            print(f"   - « {passage} »  (à réinsérer après : ...{contexte})")
    else:
        print("[OK] Aucun mot perdu ou altéré.")

    dossier = os.path.dirname(os.path.abspath(sys.argv[1])) or "."
    fichier_sortie = prochain_fichier_sortie(dossier)
    with open(fichier_sortie, "w", encoding="utf-8") as f:
        f.write(texte_rendu + "\n")
    print(f"\n[OK] Résultat enregistré : {fichier_sortie}")
