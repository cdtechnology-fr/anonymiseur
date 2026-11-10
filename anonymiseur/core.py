"""Cœur de l'anonymiseur : détection (regex + GLiNER), remplacement, restauration.

Tout tourne en local. Aucune donnée ne quitte le poste.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# --- Règles déterministes (fiables à ~100 % sur ces formats) -----------------

REGEX_RULES: list[tuple[str, re.Pattern]] = [
    ("NIR", re.compile(r"\b[12]\s?\d{2}\s?(?:0[1-9]|1[0-2])\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("IBAN", re.compile(r"\bFR\d{2}(?:\s?[0-9A-Z]{4}){5}\s?[0-9A-Z]{3}\b")),
    ("TEL", re.compile(r"(?<!\d)(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}(?!\d)")),
    ("DATE", re.compile(r"\b(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{2}\b")),
    ("DOSSIER", re.compile(r"\b[A-Z]{2,6}-\d{4}-\d{3,6}\b")),
    ("ADRESSE", re.compile(r"\b\d{5}\s+[A-ZÉÈ][\w'’-]+(?:[ -][A-ZÉÈa-zéèêëîïôûü'’-]+){0,3}")),
]

# Libellés GLiNER (zero-shot) -> type de placeholder
# (les libellés anglais donnent de bien meilleurs scores que les français : mesuré)
GLINER_LABELS = {
    "person": "PERSONNE",
    "address": "ADRESSE",
}

MODEL_NAME = "urchade/gliner_multi_pii-v1"


@dataclass
class Span:
    start: int
    end: int
    kind: str
    source: str  # "regex" ou "gliner"

    @property
    def length(self) -> int:
        return self.end - self.start


_model = None


def _gliner():
    global _model
    if _model is None:
        from gliner import GLiNER
        _model = GLiNER.from_pretrained(MODEL_NAME)
    return _model


def detect_regex(text: str) -> list[Span]:
    spans = []
    for kind, pat in REGEX_RULES:
        for m in pat.finditer(text):
            spans.append(Span(m.start(), m.end(), kind, "regex"))
    return spans


# Types du modèle bardsai gardés (le contenu de santé d'un compte rendu n'est PAS masqué)
BARDSAI_KINDS = {
    "PERSON_NAME": "PERSONNE", "PERSON_ALIAS": "PERSONNE",
    "POSTAL_ADDRESS": "ADRESSE", "LOCATION": "ADRESSE",
    "DATE_OF_BIRTH": "DATE", "EMAIL_ADDRESS": "EMAIL", "PHONE_NUMBER": "TEL",
    "PERSON_IDENTIFIER": "NIR", "DOCUMENT_IDENTIFIER": "DOSSIER",
    "BANK_ACCOUNT_IDENTIFIER": "IBAN", "ACCOUNT_IDENTIFIER": "DOSSIER",
}
_bardsai = None


def detect_bardsai(text: str) -> list[Span]:
    """Détecteur alternatif (expérimental) : modèle bardsai ONNX int8."""
    global _bardsai
    if _bardsai is None:
        import os
        import sys
        from pathlib import Path
        model = Path(os.environ.get("ANONYMISEUR_MODEL",
                                    Path.home() / ".anonymiseur/models/bardsai"))
        sys.path.insert(0, str(model))
        from pii_detector import PIIDetector
        _bardsai = PIIDetector(str(model), onnx="model_quantized.onnx")
    return [Span(s["start"], s["end"], BARDSAI_KINDS[s["label"]], "gliner")
            for s in _bardsai.predict(text) if s["label"] in BARDSAI_KINDS]


def detect_gliner(text: str, threshold: float = 0.4) -> list[Span]:
    import os
    if os.environ.get("ANONYMISEUR_NER") == "bardsai":
        return detect_bardsai(text)
    model = _gliner()
    spans = []
    # GLiNER limite la longueur : blocs d'environ 1 200 caractères, coupés aux sauts de ligne
    offset = 0
    chunk, start = "", 0
    pieces = re.split(r"(?<=\n)", text) + [None]
    for piece in pieces:
        if piece is None or (chunk and len(chunk) + len(piece) > 1200):
            if chunk.strip():
                for e in model.predict_entities(chunk, list(GLINER_LABELS), threshold=threshold):
                    spans.append(Span(start + e["start"], start + e["end"],
                                      GLINER_LABELS[e["label"]], "gliner"))
            chunk, start = "", offset
        if piece is not None:
            chunk += piece
            offset += len(piece)
    return spans


def merge(spans: list[Span]) -> list[Span]:
    """Résout les chevauchements : le regex prime, sinon le plus long."""
    spans = sorted(spans, key=lambda s: (s.start, -s.length))
    out: list[Span] = []
    for s in spans:
        if out and s.start < out[-1].end:
            prev = out[-1]
            if s.kind == prev.kind:  # même type qui se chevauche : union (rien ne se perd)
                out[-1] = Span(prev.start, max(prev.end, s.end), s.kind,
                               "regex" if "regex" in (s.source, prev.source) else s.source)
                continue
            if (s.source == "regex" and prev.source != "regex") or \
               (s.source == prev.source and s.length > prev.length):
                out[-1] = s
            continue
        out.append(s)
    return out


TITRE = r"(?:Dr|Docteur|Pr|Professeur|Madame|Monsieur|Mme|Mlle|M\.?)(?:\s*/\s*(?:Madame|Monsieur|Mme|M\.?))?"
TITRES = re.compile(rf"^(?:{TITRE}\s+)+", re.I)
# mots qui ne doivent jamais devenir des alias de nom
MOTS_VIDES = {"madame", "monsieur", "docteur", "mlle", "mme", "patient", "patiente",
              "infirmière", "infirmier", "fille", "fils"}


def _clean(value: str) -> str:
    return TITRES.sub("", value.strip(" ,.;:()"))


def _looks_technical(text: str, s: Span) -> bool:
    """Faux positifs de GLiNER sur du texte technique (chemins, identifiants...)."""
    value = text[s.start:s.end]
    before = text[s.start - 1] if s.start > 0 else " "
    after = text[s.end] if s.end < len(text) else " "
    return bool(re.search(r"[\d_@\\]", value)) or before in "/\\" or after in "/\\"


PH_RE = re.compile(r"\[([A-Z]+)_(\d+)(?:\.(\d+))?\]")


def anonymize(text: str, use_gliner: bool = True,
              table: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """Renvoie (texte anonymisé, table placeholder -> valeur d'origine).

    `table` : table existante de l'espace de travail. Les valeurs déjà connues
    gardent leur placeholder, les nouvelles sont ajoutées (la table est mise à jour).
    """
    # Les placeholders déjà présents (texte déjà anonymisé) sont intouchables : sans
    # cela, une seconde passe les prendrait pour des noms et polluerait la table.
    ph_ranges = [(m.start(), m.end()) for m in PH_RE.finditer(text)]

    def touches_placeholder(s: Span) -> bool:
        return any(s.start < b and a < s.end for a, b in ph_ranges)

    spans = detect_regex(text)
    if use_gliner:
        spans += detect_gliner(text)
    spans = merge([s for s in spans if not touches_placeholder(s)])

    # 1) Attribution des placeholders (même valeur -> même placeholder)
    by_norm: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    table = table if table is not None else {}
    for ph, value in table.items():  # reprise de la table existante
        m = PH_RE.fullmatch(ph)
        if not m:
            continue
        kind = m.group(1)
        by_norm[(kind, value.casefold())] = ph
        if m.group(3) is None:
            counters[kind] = max(counters.get(kind, 0), int(m.group(2)))

    def placeholder(kind: str, value: str) -> str:
        key = (kind, value.casefold())
        if key not in by_norm:
            counters[kind] = counters.get(kind, 0) + 1
            ph = f"[{kind}_{counters[kind]}]"
            by_norm[key] = ph
            table[ph] = value
        return by_norm[key]

    kept = []
    for s in spans:
        if s.source == "gliner" and s.kind == "PERSONNE" and _looks_technical(text, s):
            continue  # chemin, identifiant, adresse e-mail : pas un nom de personne
        kept.append(s)
    spans = kept

    for s in spans:
        value = text[s.start:s.end]
        if s.kind in ("PERSONNE", "ADRESSE", "LIEU"):
            value = _clean(value)
            if len(value) < 2:
                continue
        placeholder(s.kind, value)

    # 2) Propagation : chaque mot d'un nom de personne est aussi remplacé
    #    partout ailleurs (« Lefèvre » seul, « LEFÈVRE », « Maurice »...).
    #    Chaque mot reçoit un placeholder dérivé « [PERSONNE_2.1] » : Claude voit que
    #    c'est la même personne, et la restauration redonne exactement le mot d'origine.
    for ph, value in list(table.items()):
        if ph.startswith("[PERSONNE_") and "." not in ph:
            toks = re.findall(r"[^\W\d_]{3,}(?:-[^\W\d_]+)*", value)
            for i, tok in enumerate(toks, 1):
                key = ("PERSONNE", tok.casefold())
                if tok.casefold() in MOTS_VIDES or key in by_norm:
                    continue
                alias = f"{ph[:-1]}.{i}]"
                by_norm[key] = alias
                table[alias] = tok

    # 3) Remplacement global, valeurs les plus longues d'abord, insensible à la casse
    lookup = {v.casefold(): ph for (k, v), ph in by_norm.items()}
    ordered = sorted(lookup, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![\w])(" + "|".join(re.escape(v) for v in ordered) + r")(?![\w])",
        re.I) if ordered else None
    if pattern is None:
        return text, table

    def sub(segment: str) -> str:
        return pattern.sub(lambda m: lookup[m.group(1).casefold()], segment)

    parts, pos = [], 0
    for a, b in ph_ranges:  # on ne touche pas aux placeholders existants
        parts.append(sub(text[pos:a]))
        parts.append(text[a:b])
        pos = b
    parts.append(sub(text[pos:]))
    return "".join(parts), table


def restore(text: str, table: dict[str, str]) -> tuple[str, list[str]]:
    """Remet les valeurs d'origine. Renvoie (texte, placeholders non retrouvés)."""
    missing = [ph for ph in table if ph not in text]
    for ph in sorted(table, key=len, reverse=True):
        text = text.replace(ph, table[ph])
    return text, missing


def save_table(table: dict[str, str], path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=1)
