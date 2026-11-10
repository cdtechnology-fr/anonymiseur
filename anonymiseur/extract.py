"""Extraction du texte des documents (txt, md, csv, pdf, docx)."""
from __future__ import annotations

from pathlib import Path

TEXT_EXT = {".txt", ".md", ".csv", ".json", ".html", ".htm"}


class ExtractionError(Exception):
    pass


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in TEXT_EXT:
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        from pypdf import PdfReader
        pages = [p.extract_text() or "" for p in PdfReader(str(path)).pages]
        text = "\n\n".join(pages)
        if len(text.strip()) < 20:
            raise ExtractionError(
                "PDF sans texte exploitable (probablement un scan) : l'OCR n'est pas "
                "disponible, le document ne peut pas être traité automatiquement.")
        return text
    if ext == ".docx":
        import docx
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    raise ExtractionError(f"Format non pris en charge : {ext or '(sans extension)'} "
                          "(formats gérés : txt, md, csv, pdf, docx).")
