# anonymiseur

Anonymisation locale et transparente entre l'utilisateur et Claude Code, prévue pour une
association d'aide à domicile (comptes rendus, dossiers de soins). Distribué comme **mod** Claude Code.
**État : prototype validé sur données fictives.**

## Ce que voit chacun

- **Claude (le modèle)** ne reçoit que des placeholders : `[PERSONNE_1]`, `[NIR_1]`,
  `[TEL_2]`, `[ADRESSE_1]`... (`[PERSONNE_2.1]` = un mot isolé du nom `[PERSONNE_2]`).
- **L'utilisateur** voit les vraies valeurs dans le chat, les résultats d'outils et
  les fichiers produits. Il n'écrit aucune commande et colle ses notes normalement.

## Comment

`hooks/register.ts` (le mod) :

| Événement | Rôle |
|---|---|
| `prompt.submit`, `session.append` | anonymise le message et tout texte entrant (résultats d'outils) avant stockage et envoi |
| `tool.call` Read | redirige pdf/docx/xlsx/csv... vers la copie anonymisée (`anonymise/`), refuse les images |
| `tool.call` (tous) | remet les vraies valeurs dans les arguments (contenu écrit, chemins, commandes) |
| `ui.render` | restaure l'affichage des messages et résultats d'outils |
| `prompt.compose` | explique les placeholders au modèle |
| `session.start` | réveille le service local |

Si l'anonymisation échoue, le texte est masqué, jamais envoyé en clair.

Moteur : `engine/` (Rust, exécutable unique `ano`) : règles (NIR, téléphone, e-mail, IBAN,
dates, code postal), modèle NER `bardsai/eu-pii-anonimization-multilang-v3` (ONNX int8,
Apache-2.0), dictionnaire modifiable, extraction txt/docx/pdf, service résident local
(127.0.0.1, jeton, modèle gardé en mémoire). La table de correspondance est par dossier de
travail, dans `%LOCALAPPDATA%\ASDVO\anonymiseur\tables\` (Windows). Tout est local.

`bin/install.ps1` télécharge le moteur (release GitHub) et le modèle, empreintes SHA-256
vérifiées d'après `engine-manifest.json`. `anonymiseur/`, `poc/` et `tests/` contiennent le
prototype Python, conservé comme référence et banc de mesure du rappel.

## Installation

```
/plugin install anonymiseur --marketplace cdtechnology-fr/anonymiseur
```

Premier lancement : téléchargement du moteur et du modèle (~310 Mo, quelques minutes).
Cible : Windows 10/11 x64 avec Claude Desktop (onglet Code) ou Claude Code.

## Repli (anciennes versions sans mods)

`hooks/hooks.classic.json` + `hooks/guard.py`, `prompt_guard.py`, `session_start.py` :
règle injectée, blocage de la lecture des documents non anonymisés et du texte collé.
À copier en `hooks/hooks.json` si le mod n'est pas disponible.

## Tests

```bash
V=~/.anonymiseur/venv/bin/python
python3 poc/gen_docs.py                 # 15 dossiers patients fictifs + vérité terrain
$V -I poc/evaluate.py                   # rappel : 157/157, aller-retour exact
$V tests/e2e.py                         # CLI, service, hooks de repli, idempotence
claude plugin validate .                # structure du mod
```

Test réel : `claude -p "..." --plugin-dir .` : zéro valeur réelle dans les messages
lus par le modèle, fichier produit restauré.

## Limites

- Mesures sur documents fictifs (gabarits) : optimistes. À refaire sur de vrais textes.
- Scans et images : pas d'OCR, refusés. Livrables docx : non restaurés.
- Le fichier de transcription local de Claude Code (`~/.claude/projects/`) garde les
  sorties brutes des outils sur le poste (jamais envoyées à l'API).
- C'est de la pseudonymisation (RGPD) : la table est une donnée personnelle. Accord
  du DPO requis avant usage sur des dossiers de soins.
