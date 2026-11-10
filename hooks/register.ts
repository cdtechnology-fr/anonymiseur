// Mod « anonymiseur » : anonymisation transparente entre l'utilisateur et Claude.
//
//  - Tout ce qui entre dans la conversation (message, résultat d'outil) est anonymisé
//    par le moteur local `ano` avant d'être stocké et envoyé (session.append, prompt.submit).
//  - Un Read de document (pdf, docx...) est redirigé vers sa copie anonymisée.
//  - Ce que Claude demande aux outils (contenu écrit, chemins, commandes) est restauré :
//    les fichiers contiennent les vraies valeurs, sans étape supplémentaire.
//  - L'affichage (messages, résultats d'outils) est restauré : l'utilisateur voit les
//    vraies valeurs, le modèle ne voit que des jetons [PERSONNE_1]...
//
// Tout tourne en local. Le moteur et le modèle sont installés (et mis à jour) au
// démarrage par bin/install.ps1 d'après engine-manifest.json.
//
// Contrainte du validateur de mods : une fonction qui reçoit `$` est déclarée au
// niveau supérieur du fichier (pas dans `register`), d'où les fonctions ci-dessous.
import type { Register } from 'claude-code'

type Table = Record<string, string>

const DOC_EXT = new Set(['pdf', 'docx', 'doc', 'odt', 'rtf', 'xlsx', 'xls', 'csv'])
const IMG_EXT = new Set(['png', 'jpg', 'jpeg', 'tif', 'tiff', 'heic', 'gif', 'bmp', 'webp'])
// portes par lesquelles du texte entre dans la conversation sans qu'on l'anonymise
const SKIP_DOORS = new Set(['response', 'compaction', 'notice', 'queue', 'note'])
const MASKED = '[contenu masqué : anonymisation indisponible]'
const INSTALL_TIMEOUT_MS = 600000 // 10 min (maximum autorisé) : premier téléchargement du modèle (~300 Mo)

const RULE = `ANONYMISATION. Les données personnelles et de santé sont remplacées automatiquement, avant que tu les lises, par des jetons : [PERSONNE_1], [NIR_1], [TEL_2], [ADRESSE_1], [DOSSIER_1]... [PERSONNE_2.1] désigne un mot isolé du nom [PERSONNE_2]. Garde ces jetons exactement tels quels dans tout ce que tu écris (réponses, comptes rendus, fichiers) : ils sont remplacés par les vraies valeurs automatiquement pour l'utilisateur. Ne cherche jamais à deviner une vraie valeur. Si une donnée sensible t'apparaît quand même en clair, signale-le à l'utilisateur. Les documents scannés et les images ne peuvent pas être anonymisés : dis-le à l'utilisateur plutôt que d'insister.`

// table jeton -> valeur, tenue à jour après chaque anonymisation
let table: Table = {}
// chemin du moteur, connu après l'installation
let ano = ''
// la consigne sur les jetons n'est envoyée qu'une fois par session
let ruleSent = false

function extOf(path: string): string {
  const m = /\.([A-Za-z0-9]+)$/.exec(path)
  return m ? m[1].toLowerCase() : ''
}

function restore(text: string): string {
  let out = text
  for (const ph of Object.keys(table).sort((a, b) => b.length - a.length)) {
    out = out.split(ph).join(table[ph])
  }
  return out
}

function deepRestore(v: unknown): unknown {
  if (typeof v === 'string') return restore(v)
  if (Array.isArray(v)) return v.map(deepRestore)
  if (v !== null && typeof v === 'object') {
    return Object.fromEntries(
      Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, deepRestore(x)]),
    )
  }
  return v
}

// remet les vraies valeurs dans tous les champs texte d'une entrée d'outil
function restoreStrings<T extends object>(e: T): T {
  return Object.fromEntries(
    Object.entries(e).map(([k, v]) => [k, typeof v === 'string' ? restore(v) : v]),
  ) as T
}

// Installe le moteur si besoin (idempotent, rapide une fois installé) et renvoie son chemin.
async function ensureAno($: any): Promise<string> {
  if (ano !== '') return ano
  const root: string = $.plugin.root
  const isUnix = root.startsWith('/')
  const argv = isUnix
    ? ['sh', `${root}/bin/install.sh`]
    : ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', `${root}\\bin\\install.ps1`, '-PluginRoot', root]
  const r = await $.process.run(argv, { timeoutMs: INSTALL_TIMEOUT_MS })
  const path = String(r.stdout).trim().split(/\r?\n/).pop() ?? ''
  if (r.exitCode !== 0 || path === '') {
    throw new Error(`installation du moteur impossible : ${String(r.stderr).trim().slice(0, 300)}`)
  }
  ano = path
  return ano
}

async function anonymize($: any, text: string): Promise<string> {
  if (text.trim().length < 3) return text
  const bin = await ensureAno($)
  const r = await $.process.run([bin, 'texte'], { stdin: text, timeoutMs: 180000 })
  if (r.exitCode !== 0) throw new Error(`anonymiseur : code ${r.exitCode}`)
  const out = JSON.parse(r.stdout) as { text: string; table: Table }
  table = out.table
  return out.text
}

// blocs d'une ligne de conversation : textes et résultats d'outils
async function mapBlocks($: any, blocks: any[]): Promise<any[]> {
  return Promise.all(
    blocks.map(async (b: any) => {
      if (b.type === 'text' && typeof b.text === 'string') {
        return { ...b, text: await anonymize($, b.text) }
      }
      if (b.type === 'tool_result') {
        if (typeof b.content === 'string') return { ...b, content: await anonymize($, b.content) }
        if (Array.isArray(b.content)) return { ...b, content: await mapBlocks($, b.content) }
      }
      return b
    }),
  )
}

function maskBlocks(blocks: any[]): any[] {
  return blocks.map((b: any) =>
    b.type === 'text'
      ? { ...b, text: MASKED }
      : b.type === 'tool_result'
        ? { ...b, content: MASKED }
        : b,
  )
}

async function readDocument($: any, path: string): Promise<{ path?: string; error?: string }> {
  const bin = await ensureAno($)
  const r = await $.process.run([bin, 'doc', path], { timeoutMs: 180000 })
  const m = /^OK .* -> (.+)$/m.exec(r.stdout)
  if (r.exitCode !== 0 || !m) return { error: String(r.stdout).trim().slice(0, 300) }
  return { path: m[1].trim() }
}

async function warmUp($: any): Promise<void> {
  try {
    $.ui.status("Anonymiseur : préparation (première utilisation : quelques minutes)...")
    const bin = await ensureAno($)
    await $.process.run([bin, 'spawn']) // réveille le service pendant que l'on tape
    const r = await $.process.run([bin, 'table'])
    if (r.exitCode === 0) table = JSON.parse(r.stdout)
    $.ui.status(undefined)
  } catch (err) {
    $.ui.status(undefined)
    $.ui.toast(`Anonymiseur indisponible : ${String(err).slice(0, 160)}`)
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    ruleSent = false
    await warmUp($)
    return next(e)
  })

  // Le message de l'utilisateur : anonymisé avant d'être enregistré (même dans le
  // fichier de transcription). Une commande slash n'accepte pas de réécriture.
  // La consigne sur les jetons passe par `context` (au premier message de la session) :
  // `prompt.compose` est ignoré pour un mod utilisateur sous connexion Team.
  on('prompt.submit', async ($, e, next) => {
    if (e.command) return next(e)
    // Pièces jointes (images, fichiers glissés) : elles ne peuvent pas être anonymisées.
    if (e.attachments !== undefined && e.attachments.length > 0) {
      $.ui.toast("Pièce jointe non envoyée : indiquez plutôt le nom du fichier dans votre message.")
      return { drop: "Message non envoyé : les pièces jointes ne peuvent pas être anonymisées. Écrivez le nom du fichier (ex. notes.docx) et Claude le lira de façon sécurisée." }
    }
    // Un fichier cité avec « @chemin » serait injecté sans passer par l'anonymisation : on retire l'@,
    // Claude le lira avec l'outil Read (redirigé vers la copie anonymisée).
    const text = await anonymize($, e.text.replace(/(^|\s)@(?=[\w.\\/~-]+\.\w{2,5}\b)/g, '$1'))
    const context = ruleSent ? e.context : [...(e.context ?? []), RULE]
    ruleSent = true
    return next({ ...e, text, context })
  }).catch(($, e, next) =>
    next.called ? next(e) : { drop: 'Message non envoyé : anonymisation indisponible.' },
  )

  // Filet : tout texte qui entre dans la conversation (résultats d'outils, pièces jointes).
  on('session.append', async ($, e, next) => {
    const m = e.message
    if (SKIP_DOORS.has(e.door) || (m.role !== 'user' && m.type !== 'attachment')) return next(e)
    const content = await mapBlocks($, m.content as any[])
    return next({ ...e, message: { ...m, content: content as typeof m.content } })
  }).catch(($, e, next) => {
    // jamais de texte brut en cas d'erreur : on masque tout le texte de la ligne
    const m = e.message
    return next({ ...e, message: { ...m, content: maskBlocks(m.content as any[]) as typeof m.content } })
  })

  // Read d'un document : on lit sa copie anonymisée.
  on('tool.call', { tool: 'Read' }, async ($, e, next) => {
    const ext = extOf(e.file_path)
    if (IMG_EXT.has(ext)) {
      return { deny: "Image : l'anonymisation d'images n'est pas possible, elle ne peut pas être lue." }
    }
    if (!DOC_EXT.has(ext)) return next(e)
    const doc = await readDocument($, e.file_path)
    if (doc.path === undefined) return { deny: `Document non traitable : ${doc.error}` }
    return next({ ...e, file_path: doc.path })
  }).catch(() => ({ deny: "Anonymisation indisponible : le document n'est pas lu." }))

  // Ce que Claude demande aux outils (contenu écrit, chemins, commandes) : vraies valeurs.
  on('tool.call', ($, e, next) => next(restoreStrings(e)))

  // Affichage : l'utilisateur voit les vraies valeurs (le modèle, jamais).
  on('ui.render', { component: 'UserMessage' }, ($, e, next) =>
    next({ ...e, props: { ...e.props, text: restore(e.props.text) } }),
  )
  on('ui.render', { component: 'AssistantMessage' }, ($, e, next) =>
    next({ ...e, props: { ...e.props, text: restore(e.props.text) } }),
  )
  on('ui.render', { component: 'ToolResult' }, ($, e, next) =>
    next({ ...e, props: { ...e.props, output: deepRestore(e.props.output) } }),
  )
}
