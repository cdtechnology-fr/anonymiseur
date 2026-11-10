"""Banc de rappel du moteur Rust `ano` (mêmes dossiers fictifs et même métrique que evaluate.py).

Pour chaque document : `ano texte` anonymise, une valeur « fuit » si elle subsiste dans
le texte anonymisé ; l'aller-retour est vérifié avec `ano restaurer`.
Usage : python poc/evaluate_ano.py   (variables : ANO_BIN, ORT_DYLIB_PATH, ANO_HOME)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANO = os.environ.get("ANO_BIN", str(ROOT / "engine/target/release/ano"))
env = dict(os.environ)
home = tempfile.mkdtemp(prefix="ano-eval-")
env["ANO_HOME"] = home
env.setdefault("ANO_MODEL_DIR", str(Path.home() / ".anonymiseur/models/bardsai"))
ws = Path(tempfile.mkdtemp(prefix="ano-ws-"))

docs = sorted((ROOT / "poc/docs").glob("*.txt"))
total, leaks, rt_ok = defaultdict(int), defaultdict(list), 0
t0 = time.time()
for d in docs:
    text = d.read_text(encoding="utf-8")
    truth = json.loads(d.with_suffix(".truth.json").read_text(encoding="utf-8"))
    r = subprocess.run([ANO, "texte"], input=text, capture_output=True, text=True, cwd=ws, env=env)
    if r.returncode != 0:
        print(d.name, "ERREUR", r.stderr[-300:])
        sys.exit(1)
    anon = json.loads(r.stdout)["text"]
    for item in truth:
        total[item["type"]] += 1
        if item["valeur"].casefold() in anon.casefold():
            leaks[item["type"]].append((d.name, item["valeur"]))
    f = ws / "x.anon.txt"
    f.write_text(anon, encoding="utf-8")
    subprocess.run([ANO, "restaurer", str(f)], capture_output=True, text=True, cwd=ws, env=env)
    back = (ws / "x.restaure.txt").read_text(encoding="utf-8")
    rt_ok += back.casefold() == text.casefold()
dt = time.time() - t0
subprocess.run([ANO, "shutdown"], cwd=ws, env=env)
print(f"{len(docs)} documents en {dt:.1f}s ({dt/len(docs):.2f}s/doc)")
print(f"aller-retour identique : {rt_ok}/{len(docs)}")
print(f"{'type':16}{'total':>6}{'fuites':>7}{'rappel':>8}")
for t in sorted(total):
    n, fl = total[t], len(leaks[t])
    print(f"{t:16}{n:>6}{fl:>7}{(n-fl)/n:>8.0%}")
tot, tl = sum(total.values()), sum(len(v) for v in leaks.values())
print(f"{'TOTAL':16}{tot:>6}{tl:>7}{(tot-tl)/tot:>8.0%}")
for t, items in leaks.items():
    for name, v in items[:8]:
        print(f"  fuite [{t}] {name}: {v!r}")
