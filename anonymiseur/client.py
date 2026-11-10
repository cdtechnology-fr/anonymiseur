"""Client du service local (stdlib uniquement : importable sans le venv)."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

HOME = Path(os.environ.get("ANONYMISEUR_HOME", Path.home() / ".anonymiseur"))
INFO = HOME / "daemon.json"
LOCK = HOME / "daemon.lock"


def call(payload: dict, timeout: float = 120) -> dict | None:
    """Envoie une requête au service. Renvoie None s'il n'est pas disponible."""
    try:
        info = json.loads(INFO.read_text(encoding="utf-8"))
        s = socket.create_connection(("127.0.0.1", int(info["port"])), timeout=1)
    except (OSError, ValueError, KeyError):
        return None
    try:
        s.settimeout(timeout)
        s.sendall((json.dumps({**payload, "token": info["token"]}) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf) if buf else None
    except (OSError, ValueError):
        return None
    finally:
        s.close()
