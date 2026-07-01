#!/usr/bin/env python3
import sys
import os
import re
import fitz  # PyMuPDF

def extract_pdf_to_txt(pdf_path):
    doc = fitz.open(pdf_path)
    all_spans = []

    for page in doc:
        blocks = page.get_text("dict")["blocks"]

        for block in blocks:
            if "lines" not in block:
                continue

            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"]
                    # Détection italique
                    is_italic = span.get("italic", False) or "Italic" in span.get("font", "")
                    all_spans.append({"text": text, "is_italic": is_italic})
                # Ajouter un saut de ligne à la fin de chaque ligne
                all_spans.append({"text": "\n", "is_italic": None})

    # Classer les espaces et sauts de ligne intermédiaires
    for i, span in enumerate(all_spans):
        if span["text"].strip() == "":
            prev_italic = False
            for j in range(i - 1, -1, -1):
                if all_spans[j]["text"].strip() != "":
                    prev_italic = all_spans[j]["is_italic"]
                    break
            next_italic = False
            for j in range(i + 1, len(all_spans)):
                if all_spans[j]["text"].strip() != "":
                    next_italic = all_spans[j]["is_italic"]
                    break
            span["is_italic"] = prev_italic and next_italic

    # Fusionner les spans consécutifs de même style
    merged_spans = []
    for span in all_spans:
        if not merged_spans:
            merged_spans.append(span)
        else:
            prev = merged_spans[-1]
            if prev["is_italic"] == span["is_italic"]:
                prev["text"] += span["text"]
            else:
                merged_spans.append(span)

    # Formater le texte final
    def wrap_italic_text(text):
        leading = re.match(r"^(\s*)", text).group(1)
        trailing = re.search(r"(\s*)$", text).group(1)
        middle = text[len(leading):len(text)-len(trailing)]
        if not middle:
            return text
        return f"{leading}({middle}){trailing}"

    output_text = ""
    for span in merged_spans:
        text = span["text"]
        if span["is_italic"]:
            output_text += wrap_italic_text(text)
        else:
            output_text += text

    # Nettoyage global
    output_text = re.sub(r'\(\s*\(\s*(.*?)\s*\)\s*\)', r'(\1)', output_text)
    output_text = re.sub(r'[ \t]*\([ \t]*', ' (', output_text)
    output_text = re.sub(r'[ \t]*\)[ \t]*', ') ', output_text)

    # Construction du chemin de sortie
    base, _ = os.path.splitext(pdf_path)
    out_path = base + ".txt"

    # Écriture du fichier final
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output_text)

    print(f"Fichier généré : {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage : python3 convertpdftoabs.py fichier.pdf")
        sys.exit(1)

    pdf_path = sys.argv[1]
    extract_pdf_to_txt(pdf_path)
