#!/bin/sh
# Équivalent de install.ps1 pour le développement sous Linux : n'installe rien, affiche
# le chemin de `ano` (variable ANO_BIN, ou binaire compilé dans engine/target/release).
if [ -n "$ANO_BIN" ] && [ -x "$ANO_BIN" ]; then
  echo "$ANO_BIN"
  exit 0
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -x "$ROOT/engine/target/release/ano" ]; then
  echo "$ROOT/engine/target/release/ano"
  exit 0
fi
echo "anonymiseur : moteur ano introuvable (définir ANO_BIN)" >&2
exit 1
