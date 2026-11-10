"""Mesure le rappel de l'anonymisation sur les documents fictifs.

Une valeur « fuit » si elle apparaît encore dans le texte anonymisé.
Usage : python poc/evaluate.py [--no-gliner]
"""
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anonymiseur.core import anonymize, restore  # noqa: E402

use_gliner = "--no-gliner" not in sys.argv
docs = sorted((Path(__file__).parent / "docs").glob("*.txt"))

total = defaultdict(int)
leaks = defaultdict(list)
roundtrip_ok = 0
t0 = time.time()
for d in docs:
    text = d.read_text(encoding="utf-8")
    truth = json.loads(d.with_suffix(".truth.json").read_text(encoding="utf-8"))
    anon, table = anonymize(text, use_gliner=use_gliner)
    for item in truth:
        total[item["type"]] += 1
        if item["valeur"].casefold() in anon.casefold():
            leaks[item["type"]].append((d.name, item["valeur"]))
    back, _ = restore(anon, table)
    # la casse peut changer (« LEFÈVRE » -> « Lefèvre ») : comparaison insensible à la casse
    roundtrip_ok += back.casefold() == text.casefold()
dt = time.time() - t0

print(f"{len(docs)} documents en {dt:.1f}s ({dt/len(docs):.2f}s/doc) - gliner={use_gliner}")
print(f"aller-retour identique : {roundtrip_ok}/{len(docs)}")
print(f"{'type':16}{'total':>6}{'fuites':>7}{'rappel':>8}")
for t in sorted(total):
    n, f = total[t], len(leaks[t])
    print(f"{t:16}{n:>6}{f:>7}{(n-f)/n:>8.0%}")
tot, tl = sum(total.values()), sum(len(v) for v in leaks.values())
print(f"{'TOTAL':16}{tot:>6}{tl:>7}{(tot-tl)/tot:>8.0%}")
for t, items in leaks.items():
    for name, v in items[:6]:
        print(f"  fuite [{t}] {name}: {v!r}")
