"""SessionStart : injecte la règle d'anonymisation dans le contexte de Claude."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = f'python3 "{ROOT / "bin" / "anonymiser.py"}"'

RULE = f"""RÈGLE ANONYMISATION (obligatoire, données personnelles et de santé).
Quand tu dois travailler sur un document (PDF, Word, CSV, texte...) :
1. Ne lis JAMAIS le document d'origine. Lance d'abord : {CLI} "<fichier>"
   Il écrit une copie anonymisée dans le dossier `anonymise/` voisin (fichier .anon.txt).
2. Travaille uniquement sur ce fichier. Les valeurs sensibles y sont remplacées par des
   placeholders ([PERSONNE_1], [NIR_1], [PERSONNE_2.1]...). Garde-les tels quels dans
   tout ce que tu produis ; [PERSONNE_2.1] désigne un mot isolé du nom [PERSONNE_2].
3. Pour tout livrable, écris-le dans un fichier (txt, md, csv), puis lance :
   {CLI} restaurer "<livrable>"
   Il crée une copie `.restaure` avec les vraies valeurs, que tu ne dois pas lire.
   Indique à l'utilisateur le chemin de cette copie.
4. Dans le chat, garde les placeholders ; ne tente pas de deviner les vraies valeurs.
5. Si une donnée sensible t'apparaît quand même en clair, signale-le à l'utilisateur.
6. Documents scannés (sans texte) ou images : non pris en charge, dis-le à l'utilisateur.
Un garde-fou technique bloque la lecture des documents non anonymisés."""

# Démarre (ou installe puis démarre) le service local en arrière-plan : le modèle est
# prêt quand le premier document arrive. Ne bloque jamais le démarrage de la session.
try:
    sys.path.insert(0, str(ROOT / "bin"))
    import anonymiser
    anonymiser.spawn_warmup()
except Exception:
    pass

print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                          "additionalContext": RULE}}))
