"""UserPromptSubmit : bloque un message qui contient des données sensibles collées
dans le chat (elles partiraient en clair vers Claude).

Le message bloqué n'est pas envoyé. Filet de sécurité, pas une garantie : la
détection peut rater une donnée. Échappatoire : commencer le message par `!brut `.
Désactivation : "prompt_check": false dans ~/.anonymiseur/config.json.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("ANONYMISEUR_HOME", Path.home() / ".anonymiseur"))
LABELS = {"PERSONNE": "noms de personnes", "NIR": "numéros de sécurité sociale",
          "TEL": "numéros de téléphone", "EMAIL": "adresses e-mail", "IBAN": "IBAN",
          "ADRESSE": "adresses", "DATE": "dates", "DOSSIER": "numéros de dossier"}
# les dates seules sont trop courantes pour justifier un blocage
IGNORED = {"DATE"}


def block(counts: dict):
    found = ", ".join(f"{n} {LABELS.get(k, k)}" for k, n in counts.items() if k not in IGNORED)
    reason = (f"Message non envoyé : il contient des données personnelles collées en clair "
              f"({found}). Enregistre ton texte dans un fichier (ex. notes.txt) et demande-moi "
              f"de le traiter : il sera anonymisé avant lecture. "
              f"(Pour envoyer quand même : commence le message par « !brut ».)")
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def main():
    try:
        cfg = json.loads((HOME / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    if cfg.get("disabled") or cfg.get("prompt_check") is False:
        return
    try:
        prompt = json.load(sys.stdin).get("prompt", "")
    except ValueError:
        return
    if len(prompt.strip()) < 15 or prompt.lstrip().lower().startswith("!brut"):
        return
    sys.path.insert(0, str(ROOT))
    from anonymiseur import client, core
    resp = client.call({"cmd": "detecter", "text": prompt}, timeout=20)
    if resp is not None and "counts" in resp:
        counts = resp["counts"]  # détection complète (modèle chargé dans le service)
    else:  # service pas encore prêt : repli sur les règles (NIR, tél, e-mail, IBAN...)
        counts = {}
        for s in core.detect_regex(prompt):
            counts[s.kind] = counts.get(s.kind, 0) + 1
    if any(k not in IGNORED for k in counts):
        block(counts)


if __name__ == "__main__":
    main()
