#!/usr/bin/env python3
"""Convertit un PDF de roman en texte pour AudioBookStudio.

Stratégie (inspirée du besoin de reconstruire des phrases) :
- On IGNORE les images (get_text ne renvoie que le texte).
- Les retours à la ligne du PDF ne sont que de la MISE EN PAGE : on les
  supprime pour reconstituer le texte fluide, en ne gardant que les vraies
  coupures de PARAGRAPHE (lignes vides).
- On recolle les mots coupés en fin de ligne ("exem-\nple" → "exemple").
- On re-découpe ensuite en PHRASES à l'aide de la ponctuation (. ! ? …),
  en respectant les abréviations (M., Mme., etc.) et les guillemets fermants.
- Sortie : UNE PHRASE PAR LIGNE. Ainsi `split_book` (ligne par ligne) produit
  un segment = une phrase (fini les segments coupés en plein milieu), et
  `chunker_book` (qui rejoint puis re-découpe en 250-400 car.) reste optimal.

Sortie : <même nom que le PDF>.txt dans le même dossier (book/).
"""
import sys
import os
import re

import fitz  # PyMuPDF


# Taille cible maximale d'un chunk (caractères). 1 à 4 phrases en moyenne.
MAX_CHUNK_CHARS = 500

# Abréviations dont le point ne termine PAS une phrase.
_ABBR = [
    "M.", "MM.", "Mme.", "Mmes.", "Mlle.", "Mlles.", "Dr.", "Pr.", "Me.",
    "Mr.", "Mrs.", "Ms.", "St.", "Ste.", "etc.", "cf.", "p.", "pp.", "vol.",
    "chap.", "fig.", "art.", "No.", "n°.", "av.", "apr.", "J.-C.", "op.", "éd.",
]


def split_into_sentences(text):
    """Découpe un paragraphe en phrases sur la ponctuation forte."""
    tmp = text
    # Protéger le point des abréviations (remplacé par un caractère sentinelle).
    for a in _ABBR:
        tmp = tmp.replace(a, a[:-1] + "\x00")
    # Insérer une coupure après . ! ? … (+ éventuel guillemet/parenthèse fermant)
    # lorsqu'ils sont suivis d'un espace puis d'un début de phrase (majuscule,
    # chiffre, guillemet/parenthèse ouvrant, tiret de dialogue).
    tmp = re.sub(
        r'([.!?…]+["»”\'\)\]]?)\s+(?=[«"“\(\[A-ZÀ-ÖØ-Þ0-9—–-])',
        r'\1\n',
        tmp,
    )
    # Restaurer les points d'abréviation.
    tmp = tmp.replace("\x00", ".")
    return [s.strip() for s in tmp.split("\n") if s.strip()]


def extract_pdf_to_txt(pdf_path):
    doc = fitz.open(pdf_path)
    raw = "\n".join(page.get_text("text") for page in doc)

    # Normaliser les fins de ligne et recoller les césures de fin de ligne.
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"(\w)-\n(\w)", r"\1\2", raw)

    # Découper en paragraphes (lignes vides = coupures), joindre les lignes de
    # chaque paragraphe (mise en page → texte fluide).
    paragraphs = []
    for para in re.split(r"\n[ \t]*\n", raw):
        joined = re.sub(r"\s*\n\s*", " ", para)
        joined = re.sub(r"[ \t]{2,}", " ", joined).strip()
        if joined:
            paragraphs.append(joined)

    # Fusionner un paragraphe avec le précédent si celui-ci ne finit PAS par une
    # ponctuation forte : c'est une phrase coupée par un saut de page/colonne.
    merged = []
    for p in paragraphs:
        prev = merged[-1] if merged else ""
        ends_sentence = bool(re.search(r'[.!?…"»”\)\]]$', prev)) and not any(prev.endswith(a) for a in _ABBR)
        if merged and not ends_sentence:
            merged[-1] = prev + " " + p
        else:
            merged.append(p)

    # Reconstituer la liste des phrases, puis les REGROUPER en chunks jusqu'à
    # ~500 caractères (1 à 4 phrases). On ne coupe JAMAIS au milieu d'une phrase :
    # un chunk = un ou plusieurs phrases entières. Cible éco-ergonomique : assez
    # long pour être fluide, assez court pour rejouer sans gaspiller (VoiceBox
    # accepte jusqu'à ~800, on plafonne volontairement plus bas).
    sentences = []
    for para in merged:
        sentences.extend(split_into_sentences(para))

    chunks = []
    buf = ""
    for sent in sentences:
        if not buf:
            buf = sent
        elif len(buf) + 1 + len(sent) <= MAX_CHUNK_CHARS:
            buf += " " + sent
        else:
            chunks.append(buf)
            buf = sent
    if buf:
        chunks.append(buf)

    # Un chunk par ligne (séparés par une ligne vide) → split_book crée un
    # segment par chunk ; chunker_book (si utilisé) rejoint puis re-découpe.
    output_text = "\n\n".join(chunks).strip() + "\n"

    base, _ = os.path.splitext(pdf_path)
    out_path = base + ".txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output_text)

    avg = round(sum(len(c) for c in chunks) / len(chunks)) if chunks else 0
    print(f"[OK] Fichier généré : {out_path}")
    print(f"[INFO] {len(doc)} page(s), {len(chunks)} chunk(s) (~{avg} car./chunk, max {MAX_CHUNK_CHARS}), images ignorées.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage : python convertpdftotxt_novel.py fichier.pdf")
        sys.exit(1)
    extract_pdf_to_txt(sys.argv[1])
