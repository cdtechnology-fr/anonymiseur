"""Banc de rappel du modèle bardsai (ONNX int8) sur les dossiers fictifs.

Même métrique que evaluate.py : une valeur « fuit » si elle apparaît encore dans le
texte anonymisé. Sert à décider si le portage Rust du modèle vaut le coup.
Usage : python poc/evaluate_bardsai.py [dossier_modele]
"""
import json
import os
import sys
import time
import warnings
from collections import defaultdict
from pathlib import Path

warnings.filterwarnings("ignore")
MODEL = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / ".anonymiseur/models/bardsai")
sys.path.insert(0, str(MODEL))
from pii_detector import PIIDetector  # noqa: E402

det = PIIDetector(str(MODEL), onnx="model_quantized.onnx")
docs = sorted((Path(__file__).parent / "docs").glob("*.txt"))
total, leaks = defaultdict(int), defaultdict(list)
t0 = time.time()
for d in docs:
    text = d.read_text(encoding="utf-8")
    truth = json.loads(d.with_suffix(".truth.json").read_text(encoding="utf-8"))
    spans = det.predict(text)
    out, last = [], 0
    for sp in spans:  # remplacement de tout ce qui est détecté, quel que soit le type
        out += [text[last:sp["start"]], "[X]"]
        last = sp["end"]
    anon = "".join(out + [text[last:]])
    for item in truth:
        total[item["type"]] += 1
        if item["valeur"].casefold() in anon.casefold():
            leaks[item["type"]].append((d.name, item["valeur"]))
dt = time.time() - t0
print(f"{len(docs)} documents en {dt:.1f}s ({dt/len(docs):.2f}s/doc)")
print(f"{'type':16}{'total':>6}{'fuites':>7}{'rappel':>8}")
for t in sorted(total):
    n, f = total[t], len(leaks[t])
    print(f"{t:16}{n:>6}{f:>7}{(n-f)/n:>8.0%}")
tot, tl = sum(total.values()), sum(len(v) for v in leaks.values())
print(f"{'TOTAL':16}{tot:>6}{tl:>7}{(tot-tl)/tot:>8.0%}")
for t, items in leaks.items():
    for name, v in items[:8]:
        print(f"  fuite [{t}] {name}: {v!r}")
