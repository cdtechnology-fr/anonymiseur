"""Test de bout en bout du plugin (CLI + hook), dans un dossier temporaire.

Usage : .venv/bin/python tests/e2e.py   (ANONYMISEUR_PYTHON = ce même interpréteur)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "poc" / "docs"
tmp = Path(tempfile.mkdtemp(prefix="ano-e2e-"))
env = dict(os.environ, ANONYMISEUR_HOME=str(tmp / "home"), ANONYMISEUR_PYTHON=sys.executable)
ws = tmp / "ws"
ws.mkdir()
fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def cli(*args):
    return subprocess.run([sys.executable, str(ROOT / "bin" / "anonymiser.py"), *args],
                          cwd=ws, env=env, capture_output=True, text=True)


def guard(tool, inp):
    r = subprocess.run([sys.executable, str(ROOT / "hooks" / "guard.py")], env=env,
                       input=json.dumps({"tool_name": tool, "tool_input": inp}),
                       capture_output=True, text=True)
    return "deny" in r.stdout


import time  # noqa: E402


def prompt_blocked(prompt):
    t0 = time.time()
    r = subprocess.run([sys.executable, str(ROOT / "hooks" / "prompt_guard.py")], env=env,
                       input=json.dumps({"prompt": prompt}), capture_output=True, text=True)
    return '"block"' in r.stdout, time.time() - t0, r.stdout


# Phase 1 : service pas démarré -> repli sur les regex (NIR bloqué, nom seul non détecté)
b, _, out = prompt_blocked("Voici mes notes : Mme Odette Garnier, NIR 2 41 05 91 123 456 78, a chuté hier.")
check("repli sans service : NIR bloqué", b, out)
b, _, _ = prompt_blocked("Voici ses notes de visite : Odette Garnier est tombée dans sa cuisine.")
check("repli sans service : nom seul non détecté (limite connue)", not b)

# Document 1 (txt) et document 2 (docx, même patient que le doc 02 : mêmes noms)
shutil.copy(DOCS / "01.txt", ws / "bilan.txt")
import docx  # noqa: E402
d = docx.Document()
for line in (DOCS / "02.txt").read_text(encoding="utf-8").splitlines():
    d.add_paragraph(line)
d.save(ws / "visite.docx")

r = cli("bilan.txt", "visite.docx")
print(r.stdout.strip(), r.stderr.strip()[-300:])
check("anonymiser sort 0", r.returncode == 0, r.stderr[-300:])
a1 = (ws / "anonymise" / "bilan.anon.txt").read_text(encoding="utf-8")
a2 = (ws / "anonymise" / "visite.anon.txt").read_text(encoding="utf-8")
for v in ("Lefèvre", "Maurice", "Aubry", "Diallo", "1 38 04 91 214 242 23", "06 11 75 54 04",
          "2 rue des Lilas", "Montlhéry", "SSIAD-2026-1434"):
    check(f"valeur absente de la copie : {v}", v.casefold() not in (a1 + a2).casefold())
check("console sans valeur sensible", "Lefèvre" not in r.stdout and "06 11" not in r.stdout)
check("même personne = même placeholder entre 2 documents",
      "[PERSONNE_" in a1 and a1.count("[PERSONNE_") > 0 and
      set(__import__("re").findall(r"\[PERSONNE_\d+\]", a2)) <=
      set(__import__("re").findall(r"\[PERSONNE_\d+\]", a1 + a2)))

# Idempotence : repasser un texte déjà anonymisé ne change rien et ne pollue pas la table
sys.path.insert(0, str(ROOT))
from anonymiseur.core import anonymize as _anon  # noqa: E402
os.environ["ANONYMISEUR_HOME"] = env["ANONYMISEUR_HOME"]
from anonymiseur import cli as _cli  # noqa: E402
_cli.HOME = Path(env["ANONYMISEUR_HOME"])
_t = _cli.load_table(ws)
_again, _t2 = _anon(a1 + "\n" + a2, table=dict(_t))
check("idempotence : texte déjà anonymisé inchangé", _again == a1 + "\n" + a2)
check("idempotence : table inchangée", _t2 == _t, f"{len(_t)} -> {len(_t2)}")

# Livrable écrit par « Claude » avec placeholders, puis restauration
liv = ws / "synthese.md"
_persons = [p for p in _t if p.startswith("[PERSONNE_") and "." not in p]
_tels = [p for p in _t if p.startswith("[TEL_")]
liv.write_text(f"Synthèse pour {_persons[0]} : suivi par {_persons[1]}, tél. {_tels[0]}.\n",
               encoding="utf-8")
r = cli("restaurer", "synthese.md")
print(r.stdout.strip())
rest = (ws / "synthese.restaure.md").read_text(encoding="utf-8")
check("restauration : placeholders résolus", "[" not in rest, rest)
check("restauration : valeurs d'origine revenues", "Maurice" in rest or "Lefèvre" in rest, rest)

# Garde-fou
check("hook bloque Read pdf d'origine", guard("Read", {"file_path": "/x/dossier.pdf"}))
check("hook bloque Read docx d'origine", guard("Read", {"file_path": str(ws / "visite.docx")}))
check("hook autorise Read .anon.txt", not guard("Read", {"file_path": str(ws / "anonymise" / "bilan.anon.txt")}))
check("hook autorise Read code", not guard("Read", {"file_path": "/x/app.py"}))
check("hook bloque Read fichier restauré", guard("Read", {"file_path": str(ws / "synthese.restaure.md")}))
check("hook bloque Read table", guard("Read", {"file_path": str(tmp / "home" / "tables" / "x.map.json")}))
check("hook bloque Bash cat pdf", guard("Bash", {"command": "cat /x/dossier.pdf"}))
check("hook bloque Bash python lisant docx", guard("Bash", {"command": "python3 -c \"open('/x/a.docx')\""}))
check("hook autorise Bash ls pdf", not guard("Bash", {"command": "ls /x/*.pdf"}))
check("hook autorise l'anonymiseur lui-même", not guard("Bash", {"command": f"python3 \"{ROOT}/bin/anonymiser.py\" /x/a.pdf"}))
check("hook bloque lecture de la table", guard("Bash", {"command": "cat ~/.anonymiseur/tables/abc.map.json"}))

NIR_PROMPT = "Voici mes notes : Mme Odette Garnier, NIR 2 41 05 91 123 456 78, a chuté hier."
NAME_PROMPT = "Voici ses notes de visite : Odette Garnier est tombée dans sa cuisine."

# Phase 2 : le service est démarré (lancé par la CLI en repli) -> détection complète
for _ in range(120):
    if subprocess.run([sys.executable, "-c",
                       "import sys; sys.path.insert(0, %r); from anonymiseur import client; "
                       "sys.exit(0 if client.call({'cmd':'ping'}) else 1)" % str(ROOT)],
                      env=env).returncode == 0:
        break
    time.sleep(2)
else:
    check("service local démarré", False)

b, dt, out = prompt_blocked(NIR_PROMPT)
check("prompt avec NIR + nom bloqué", b, out)
check("message de blocage sans valeur", "Garnier" not in out and "2 41 05" not in out)
b, dt2, _ = prompt_blocked(NAME_PROMPT)
check("prompt avec un nom seul bloqué (service)", b)
b, dt3, _ = prompt_blocked("Peux-tu résumer le document notes_visite.txt en cinq lignes ?")
check("prompt normal autorisé", not b)
b, _, _ = prompt_blocked("!brut Odette Garnier 06 12 34 56 78 : test volontaire")
check("échappatoire !brut", not b)
check("hook rapide avec le service (<2 s)", max(dt, dt2, dt3) < 2, f"{dt:.1f}/{dt2:.1f}/{dt3:.1f}s")
t0 = time.time()
r = cli("bilan.txt")
check("anonymiser rapide via le service (<3 s)", time.time() - t0 < 3 and r.returncode == 0,
      f"{time.time() - t0:.1f}s {r.stdout[-200:]}")
print(f"durées : hook {dt:.2f}/{dt2:.2f}/{dt3:.2f}s, anonymiser {time.time() - t0:.2f}s")
subprocess.run([sys.executable, "-c",
                "import sys; sys.path.insert(0, %r); from anonymiseur import client; "
                "client.call({'cmd':'shutdown'})" % str(ROOT)], env=env)

shutil.rmtree(tmp, ignore_errors=True)
print("\nÉCHECS :", fails if fails else "aucun")
sys.exit(1 if fails else 0)
