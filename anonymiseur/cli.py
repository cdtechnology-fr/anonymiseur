"""Commandes `anonymiser` et `restaurer`.

La sortie console ne contient JAMAIS de valeur sensible (Claude la lit) :
uniquement des chemins et des comptes par type.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from .core import PH_RE, anonymize, restore
from .extract import ExtractionError, extract_text

HOME = Path(os.environ.get("ANONYMISEUR_HOME", Path.home() / ".anonymiseur"))
OUT_DIR = "anonymise"


def table_path(workspace: Path) -> Path:
    key = hashlib.sha1(str(workspace.resolve()).encode()).hexdigest()[:12]
    return HOME / "tables" / f"{key}.map.json"


def load_table(workspace: Path) -> dict[str, str]:
    p = table_path(workspace)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_table(workspace: Path, table: dict[str, str]) -> None:
    p = table_path(workspace)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def cmd_anonymiser(files: list[str]) -> int:
    ws = Path.cwd()
    table = load_table(ws)
    rc = 0
    for f in files:
        src = Path(f).resolve()
        try:
            text = extract_text(src)
        except (ExtractionError, FileNotFoundError) as e:
            print(f"ERREUR {src.name} : {e}")
            rc = 1
            continue
        anon, table = anonymize(text, table=table)
        out = src.parent / OUT_DIR / f"{src.stem}.anon.txt"
        out.parent.mkdir(exist_ok=True)
        out.write_text(anon, encoding="utf-8")
        used = Counter(m.group(1) for m in PH_RE.finditer(anon))
        resume = ", ".join(f"{n} {k}" for k, n in sorted(used.items())) or "aucune donnée détectée"
        print(f"OK {src.name} -> {out}")
        print(f"   remplacements : {resume}")
        if not used:
            print("   ATTENTION : rien détecté, vérifier que le document est bien du texte.")
    save_table(ws, table)
    return rc


def cmd_restaurer(files: list[str]) -> int:
    ws = Path.cwd()
    table = load_table(ws)
    if not table:
        print("ERREUR : aucune table de correspondance pour ce dossier de travail "
              "(lancer `anonymiser` depuis le même dossier).")
        return 1
    for f in files:
        src = Path(f).resolve()
        text = src.read_text(encoding="utf-8")
        out_text, _ = restore(text, table)
        unknown = sorted({m.group(0) for m in PH_RE.finditer(out_text)})
        stem = src.stem.removesuffix(".anon")
        out = src.with_name(f"{stem}.restaure{src.suffix}")
        out.write_text(out_text, encoding="utf-8")
        print(f"OK {src.name} -> {out}")
        if unknown:
            print(f"   ATTENTION placeholders non résolus : {', '.join(unknown)}")
    return 0


def detect_counts(text: str) -> dict[str, int]:
    """Nombre de données sensibles par type (jamais les valeurs)."""
    _, table = anonymize(text, table={})
    return dict(Counter(PH_RE.fullmatch(ph).group(1) for ph in table if "." not in ph))


def anonymize_text(text: str, cwd: str) -> dict:
    """Anonymise un texte (prompt, résultat d'outil) avec la table du dossier de travail.
    Renvoie {text, table} : la table sert au mod à restaurer l'affichage."""
    ws = Path(cwd)
    table = load_table(ws)
    anon, table = anonymize(text, table=table)
    save_table(ws, table)
    return {"text": anon, "table": table}


def main(argv: list[str]) -> int:
    if argv == ["texte"]:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(anonymize_text(sys.stdin.read(), os.getcwd()), ensure_ascii=False))
        return 0
    if argv == ["table"]:
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(load_table(Path.cwd()), ensure_ascii=False))
        return 0
    if len(argv) >= 2 and argv[0] == "restaurer":
        return cmd_restaurer(argv[1:])
    if argv and argv[0] == "anonymiser":
        argv = argv[1:]
    if not argv:
        print("usage : anonymiser.py [anonymiser] FICHIER...  |  anonymiser.py restaurer FICHIER...")
        return 2
    return cmd_anonymiser(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
