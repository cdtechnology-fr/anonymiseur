"""Service local : garde le modèle GLiNER en mémoire pour répondre en <1 s.

Écoute uniquement sur 127.0.0.1, port éphémère, jeton aléatoire dans
~/.anonymiseur/daemon.json (droits 600). S'arrête seul après 1 h d'inactivité.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import secrets
import socketserver
import time

from . import cli, core
from .client import INFO, LOCK

IDLE_SECONDS = 3600


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            req = json.loads(self.rfile.readline())
        except ValueError:
            return
        if req.get("token") != self.server.token:
            return
        self.server.last = time.time()
        cmd = req.get("cmd")
        try:
            if cmd == "ping":
                resp = {"ok": True}
            elif cmd == "detecter":
                resp = {"counts": cli.detect_counts(req.get("text", ""))}
            elif cmd == "texte":
                resp = cli.anonymize_text(req.get("text", ""), req["cwd"])
            elif cmd == "anonymiser":
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    os.chdir(req["cwd"])
                    rc = cli.cmd_anonymiser(req["files"])
                resp = {"out": buf.getvalue(), "rc": rc}
            elif cmd == "shutdown":
                self.server.last = 0
                resp = {"ok": True}
            else:
                resp = {"error": "commande inconnue"}
        except Exception as e:  # le service ne doit jamais tomber sur un document
            resp = {"out": f"ERREUR interne : {type(e).__name__}\n", "rc": 1, "counts": {}}
        self.wfile.write((json.dumps(resp) + "\n").encode("utf-8"))


def serve() -> None:
    INFO.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()))
    try:
        core._gliner()  # chargement du modèle avant d'annoncer le service
        srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        srv.token = secrets.token_hex(16)
        srv.last = time.time()
        srv.timeout = 30
        INFO.write_text(json.dumps({"port": srv.server_address[1], "token": srv.token,
                                    "pid": os.getpid()}), encoding="utf-8")
        try:
            os.chmod(INFO, 0o600)
        except OSError:
            pass
        while time.time() - srv.last < IDLE_SECONDS:
            srv.handle_request()
        srv.server_close()
    finally:
        for p in (INFO, LOCK):
            try:
                p.unlink()
            except OSError:
                pass
