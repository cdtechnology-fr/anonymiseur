"""Point d'entrée multiplateforme.

    python anonymiser.py FICHIER...            anonymise (via le service local)
    python anonymiser.py restaurer FICHIER...  restaure (sans modèle, instantané)
    python anonymiser.py warmup                installe l'environnement si besoin
                                               et démarre le service local

Premier lancement : création d'un venv dans ~/.anonymiseur/venv (~1 Go avec le
modèle, quelques minutes). Le service garde ensuite le modèle en mémoire.
"""
import json
import os
import subprocess
import sys
import time
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from anonymiseur import client  # noqa: E402  (stdlib uniquement)

HOME = client.HOME
VENV = HOME / "venv"
DEPS = ["gliner", "pypdf", "python-docx", "tiktoken", "sentencepiece", "protobuf"]
TORCH_CPU = "https://download.pytorch.org/whl/cpu"
INSTALL_LOCK = HOME / "installing.lock"
QUIET_ENV = dict(TOKENIZERS_PARALLELISM="false", HF_HUB_DISABLE_PROGRESS_BARS="1",
                 TRANSFORMERS_VERBOSITY="error", HF_HUB_DISABLE_TELEMETRY="1")


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_env() -> Path:
    override = os.environ.get("ANONYMISEUR_PYTHON")  # tests / installation spécifique
    if override:
        return Path(override)
    py = venv_python()
    if py.exists() and not INSTALL_LOCK.exists():
        return py
    HOME.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(INSTALL_LOCK, os.O_CREAT | os.O_EXCL)
        os.close(fd)
    except FileExistsError:  # une autre installation est en cours : on l'attend
        for _ in range(900):
            if not INSTALL_LOCK.exists():
                return py
            time.sleep(2)
        return py
    try:
        print("Première utilisation : installation de l'anonymiseur (quelques minutes)...",
              file=sys.stderr)
        venv.create(VENV, with_pip=True)
        subprocess.check_call([str(py), "-m", "pip", "install", "-q", "--extra-index-url",
                               TORCH_CPU, *DEPS])
    finally:
        INSTALL_LOCK.unlink(missing_ok=True)
    return py


def child_env() -> dict:
    return dict(os.environ, **QUIET_ENV)


def run_in_venv(py: Path, code: str, *args: str) -> int:
    return subprocess.call([str(py), "-I", "-W", "ignore", "-c",
                            f"import sys; sys.path.insert(0, {str(ROOT)!r}); {code}", *args],
                           env=child_env())


def spawn_warmup() -> None:
    """Lance `warmup` en arrière-plan, détaché de la session."""
    if client.LOCK.exists() and time.time() - client.LOCK.stat().st_mtime < 180:
        return  # un démarrage est déjà en cours
    kw = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": 0x00000008 | 0x00000200}  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "warmup"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kw)


def main(argv: list[str]) -> int:
    if argv[:1] == ["warmup"]:
        if client.call({"cmd": "ping"}):
            return 0
        py = ensure_env()
        return run_in_venv(py, "from anonymiseur.daemon import serve; serve()")
    if argv[:1] in (["restaurer"], ["table"]):  # stdlib seulement : pas besoin du venv
        from anonymiseur.cli import main as cli_main
        return cli_main(argv)
    if argv == ["spawn"]:  # démarre le service en arrière-plan et rend la main
        spawn_warmup()
        return 0
    if argv == ["texte"]:  # texte sur stdin -> JSON {text, table}
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        text = sys.stdin.read()
        resp = client.call({"cmd": "texte", "text": text, "cwd": os.getcwd()}, timeout=170)
        if resp is not None and "text" in resp:
            print(json.dumps(resp, ensure_ascii=False))
            return 0
        spawn_warmup()  # service absent : repli direct (lent la première fois)
        py = ensure_env()
        r = subprocess.run([str(py), "-I", "-W", "ignore", "-c",
                            f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
                            "from anonymiseur.cli import main; sys.exit(main(['texte']))"],
                           input=text, capture_output=True, text=True, encoding="utf-8",
                           env=child_env())
        sys.stdout.write(r.stdout)
        return r.returncode
    files = argv[1:] if argv[:1] == ["anonymiser"] else argv
    if not files:
        print("usage : anonymiser.py FICHIER... | restaurer FICHIER... | warmup")
        return 2
    resp = client.call({"cmd": "anonymiser", "cwd": os.getcwd(),
                        "files": [str(Path(f).resolve()) for f in files]})
    if resp is not None:
        print(resp.get("out", ""), end="")
        return resp.get("rc", 1)
    spawn_warmup()  # pour les appels suivants
    py = ensure_env()  # repli : exécution directe (lente la première fois)
    return run_in_venv(py, "from anonymiseur.cli import main; sys.exit(main(sys.argv[1:]))",
                       "anonymiser", *files)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
