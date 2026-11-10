"""Compare les entités du moteur Rust (`ano ner`) à celles du détecteur Python de référence.

Usage : python poc/compare_ner.py  (variables : ORT_DYLIB_PATH, ANO_BIN)
"""
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
MODEL = Path.home() / ".anonymiseur/models/bardsai"
sys.path.insert(0, str(MODEL))
from pii_detector import PIIDetector  # noqa: E402

det = PIIDetector(str(MODEL), onnx="model_quantized.onnx")
ano = os.environ.get("ANO_BIN", str(ROOT / "engine/target/release/ano"))
same = diff = 0
for d in sorted((ROOT / "poc/docs").glob("*.txt")):
    text = d.read_text(encoding="utf-8")
    py = {(e["label"], e["text"]) for e in det.predict(text)}
    r = subprocess.run([ano], input=text, capture_output=True, text=True)
    if r.returncode != 0:
        print(d.name, "ERREUR", r.stderr[-300:])
        diff += 1
        continue
    rs = {(e["label"], e["text"]) for e in json.loads(r.stdout)}
    if py == rs:
        same += 1
    else:
        diff += 1
        print(d.name, "seulement Python:", sorted(py - rs)[:5], "| seulement Rust:", sorted(rs - py)[:5])
print(f"identiques : {same}/{same + diff}")
