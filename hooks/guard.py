"""Garde-fou PreToolUse : empêche Claude de lire un document non anonymisé,
une table de correspondance ou un fichier restauré.

C'est un filet de sécurité, pas une preuve : il couvre Read, Grep et les
commandes Bash qui citent un document protégé. Un utilisateur qui colle du texte
brut dans le chat n'est pas couvert.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = f'python3 "{ROOT / "bin" / "anonymiser.py"}"'
HOME = Path(os.environ.get("ANONYMISEUR_HOME", Path.home() / ".anonymiseur"))

PROTECTED = {".pdf", ".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".csv",
             ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic"}
HARMLESS = {"ls", "mv", "cp", "rm", "mkdir", "stat", "du", "file", "pwd", "cd", "test"}


def load_config():
    try:
        return json.loads((HOME / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def deny(reason: str):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
    sys.exit(0)


def check_path(raw: str, protected: set):
    p = Path(raw).expanduser()
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    if HOME in resolved.parents or resolved == HOME:
        deny("Accès refusé : ce dossier contient les tables de correspondance (données "
             "personnelles), il ne doit jamais être lu.")
    if ".restaure." in p.name:
        deny("Accès refusé : ce fichier contient les vraies données restaurées. Il est "
             "destiné à l'utilisateur, pas à Claude.")
    if p.suffix.lower() in protected and p.parent.name != "anonymise":
        deny(f"Ce document n'a pas été anonymisé : ne le lis pas directement. "
             f"Lance d'abord `{CLI} \"{raw}\"`, puis lis le fichier .anon.txt indiqué "
             f"dans le dossier `anonymise/`.")


def check_bash(cmd: str, protected: set):
    if re.search(r"\.anonymiseur\b|\.map\.json", cmd):
        deny("Accès refusé : les tables de correspondance ne doivent jamais être lues.")
    if re.search(r"\.restaure\.", cmd) and not re.match(r"\s*(ls|rm|mv|cp)\b", cmd):
        deny("Accès refusé : les fichiers .restaure. contiennent les vraies données.")
    if "anonymiser.py" in cmd:
        return
    pat = re.compile(r"\S+(?:%s)(?=[\s'\"]|$)" % "|".join(re.escape(e) for e in protected), re.I)
    for seg in re.split(r"[;&|]+", cmd):
        words = seg.split()
        if words and words[0] in HARMLESS:
            continue
        m = pat.search(seg)
        if m and "/anonymise/" not in m.group(0):
            deny(f"Cette commande lit un document non anonymisé. Lance d'abord "
                 f"`{CLI} <fichier>` et travaille sur le fichier .anon.txt produit.")


def main():
    cfg = load_config()
    if cfg.get("disabled"):
        return
    protected = {e.lower() for e in cfg.get("protected_extensions", PROTECTED)}
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    tool, inp = data.get("tool_name"), data.get("tool_input", {})
    if tool == "Read" and inp.get("file_path"):
        check_path(inp["file_path"], protected)
    elif tool == "Grep" and inp.get("path"):
        check_path(inp["path"], protected)
    elif tool == "Bash" and inp.get("command"):
        check_bash(inp["command"], protected)


if __name__ == "__main__":
    main()
