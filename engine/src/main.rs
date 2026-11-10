//! `ano` : moteur d'anonymisation local (un seul exécutable).
//!
//!   ano texte                 texte sur stdin -> JSON {text, table} (table du dossier courant)
//!   ano doc FICHIER...        anonymise des documents -> dossier `anonymise/` voisin
//!   ano restaurer FICHIER...  remet les vraies valeurs -> FICHIER.restaure.ext
//!   ano table                 table de correspondance du dossier courant (JSON)
//!   ano spawn | serve | shutdown   service résident (modèle gardé en mémoire)
//!   ano ner                   mise au point : entités brutes du modèle, sur stdin
mod core;
mod extract;
mod ner;

use anyhow::{Context, Result};
use core::{Engine, Table};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const IDLE_SECONDS: u64 = 3600;

// ---------- chemins ----------

fn data_dir() -> PathBuf {
    if let Ok(d) = std::env::var("ANO_HOME") {
        return PathBuf::from(d);
    }
    if cfg!(windows) {
        if let Ok(d) = std::env::var("LOCALAPPDATA") {
            return PathBuf::from(d).join("ASDVO").join("anonymiseur");
        }
    }
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| ".".into())).join(".anonymiseur")
}

fn model_dir() -> PathBuf {
    std::env::var("ANO_MODEL_DIR").map(PathBuf::from).unwrap_or_else(|_| data_dir().join("models").join("bardsai"))
}

fn ort_lib() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("ORT_DYLIB_PATH") {
        return Some(PathBuf::from(p));
    }
    let exe_dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let name = if cfg!(windows) { "onnxruntime.dll" } else { "libonnxruntime.so" };
    let p = exe_dir.join(name);
    p.exists().then_some(p)
}

fn table_path(workspace: &Path) -> PathBuf {
    let digest = Sha256::digest(workspace.to_string_lossy().as_bytes());
    let key: String = digest.iter().take(6).map(|b| format!("{b:02x}")).collect();
    data_dir().join("tables").join(format!("{key}.map.json"))
}

fn load_table(workspace: &Path) -> Table {
    std::fs::read_to_string(table_path(workspace))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_table(workspace: &Path, table: &Table) -> Result<()> {
    let p = table_path(workspace);
    std::fs::create_dir_all(p.parent().unwrap())?;
    std::fs::write(&p, serde_json::to_string_pretty(table)?)?;
    Ok(())
}

/// Dictionnaire de l'association (noms de soignants, communes...) : `dictionnaire.json`
/// de la forme {"PERSONNE": ["Sophie Martin", ...], "ADRESSE": ["Linas", ...]}.
fn load_dictionary() -> Vec<(String, Vec<String>)> {
    let Ok(s) = std::fs::read_to_string(data_dir().join("dictionnaire.json")) else { return vec![] };
    let Ok(v) = serde_json::from_str::<std::collections::BTreeMap<String, Vec<String>>>(&s) else { return vec![] };
    v.into_iter().map(|(k, vals)| (k, vals.iter().map(|x| x.to_lowercase()).collect())).collect()
}

fn load_engine() -> Result<Engine> {
    let ner = ner::Ner::load(&model_dir(), ort_lib().as_deref())
        .context("modèle introuvable ou illisible (dossier du modèle, bibliothèque onnxruntime)")?;
    Engine::new(Some(ner), load_dictionary())
}

// ---------- opérations (communes au service et au mode direct) ----------

fn op_texte(engine: &mut Engine, text: &str, cwd: &Path) -> Result<Value> {
    let mut table = load_table(cwd);
    let out = engine.anonymize(text, &mut table)?;
    save_table(cwd, &table)?;
    Ok(json!({"text": out, "table": table}))
}

fn op_doc(engine: &mut Engine, files: &[String], cwd: &Path) -> (String, i32) {
    let mut table = load_table(cwd);
    let (mut out, mut rc) = (String::new(), 0);
    for f in files {
        let src = PathBuf::from(f);
        let name = src.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
        let text = match extract::extract_text(&src) {
            Ok(t) => t,
            Err(e) => {
                out.push_str(&format!("ERREUR {name} : {e}\n"));
                rc = 1;
                continue;
            }
        };
        let anon = match engine.anonymize(&text, &mut table) {
            Ok(a) => a,
            Err(e) => {
                out.push_str(&format!("ERREUR {name} : anonymisation impossible ({e})\n"));
                rc = 1;
                continue;
            }
        };
        let dir = src.parent().unwrap_or(Path::new(".")).join("anonymise");
        let stem = src.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
        let dest = dir.join(format!("{stem}.anon.txt"));
        if std::fs::create_dir_all(&dir).and_then(|_| std::fs::write(&dest, &anon)).is_err() {
            out.push_str(&format!("ERREUR {name} : écriture impossible\n"));
            rc = 1;
            continue;
        }
        let used = engine.placeholder_kinds(&anon);
        let summary = if used.is_empty() {
            "aucune donnée détectée".to_string()
        } else {
            used.iter().map(|(k, n)| format!("{n} {k}")).collect::<Vec<_>>().join(", ")
        };
        out.push_str(&format!("OK {name} -> {}\n   remplacements : {summary}\n", dest.display()));
        if used.is_empty() {
            out.push_str("   ATTENTION : rien détecté, vérifier que le document est bien du texte.\n");
        }
    }
    let _ = save_table(cwd, &table);
    (out, rc)
}

fn handle(engine: &mut Engine, req: &Value) -> Value {
    let cwd = PathBuf::from(req["cwd"].as_str().unwrap_or("."));
    match req["cmd"].as_str() {
        Some("ping") => json!({"ok": true}),
        Some("texte") => op_texte(engine, req["text"].as_str().unwrap_or(""), &cwd)
            .unwrap_or_else(|e| json!({"error": e.to_string()})),
        Some("doc") => {
            let files: Vec<String> =
                req["files"].as_array().map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default();
            let (out, rc) = op_doc(engine, &files, &cwd);
            json!({"out": out, "rc": rc})
        }
        _ => json!({"error": "commande inconnue"}),
    }
}

// ---------- service résident ----------

fn daemon_info() -> PathBuf {
    data_dir().join("daemon.json")
}
fn daemon_lock() -> PathBuf {
    data_dir().join("daemon.lock")
}

fn call_daemon(req: &Value, timeout: Duration) -> Option<Value> {
    let info: Value = serde_json::from_str(&std::fs::read_to_string(daemon_info()).ok()?).ok()?;
    let addr = format!("127.0.0.1:{}", info["port"].as_u64()?);
    let mut stream = TcpStream::connect_timeout(&addr.parse().ok()?, Duration::from_secs(1)).ok()?;
    stream.set_read_timeout(Some(timeout)).ok()?;
    let mut req = req.clone();
    req["token"] = info["token"].clone();
    stream.write_all(format!("{req}\n").as_bytes()).ok()?;
    let mut line = String::new();
    BufReader::new(stream).read_line(&mut line).ok()?;
    serde_json::from_str(&line).ok()
}

fn serve() -> Result<()> {
    std::fs::create_dir_all(data_dir())?;
    std::fs::write(daemon_lock(), std::process::id().to_string())?;
    let result = (|| -> Result<()> {
        let mut engine = load_engine()?; // modèle chargé avant d'annoncer le service
        let listener = TcpListener::bind("127.0.0.1:0")?;
        listener.set_nonblocking(true)?;
        let mut raw = [0u8; 16];
        getrandom::fill(&mut raw).map_err(|e| anyhow::anyhow!("aléa: {e}"))?;
        let token: String = raw.iter().map(|b| format!("{b:02x}")).collect();
        std::fs::write(
            daemon_info(),
            json!({"port": listener.local_addr()?.port(), "token": token, "pid": std::process::id()}).to_string(),
        )?;
        let mut last = Instant::now();
        while last.elapsed() < Duration::from_secs(IDLE_SECONDS) {
            match listener.accept() {
                Ok((stream, _)) => {
                    last = Instant::now();
                    stream.set_nonblocking(false)?;
                    stream.set_read_timeout(Some(Duration::from_secs(30)))?;
                    let mut line = String::new();
                    if BufReader::new(&stream).read_line(&mut line).is_err() {
                        continue;
                    }
                    let Ok(req) = serde_json::from_str::<Value>(&line) else { continue };
                    if req["token"].as_str() != Some(token.as_str()) {
                        continue;
                    }
                    if req["cmd"].as_str() == Some("shutdown") {
                        let _ = (&stream).write_all(b"{\"ok\":true}\n");
                        break;
                    }
                    let resp = handle(&mut engine, &req);
                    let _ = (&stream).write_all(format!("{resp}\n").as_bytes());
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(Duration::from_millis(40)),
                Err(e) => return Err(e.into()),
            }
        }
        Ok(())
    })();
    let _ = std::fs::remove_file(daemon_info());
    let _ = std::fs::remove_file(daemon_lock());
    result
}

fn spawn_service() -> Result<()> {
    if call_daemon(&json!({"cmd": "ping"}), Duration::from_secs(2)).is_some() {
        return Ok(());
    }
    if let Ok(meta) = std::fs::metadata(daemon_lock()) {
        if meta.modified().ok().and_then(|m| m.elapsed().ok()).map_or(false, |e| e < Duration::from_secs(180)) {
            return Ok(()); // un démarrage est déjà en cours
        }
    }
    let mut cmd = std::process::Command::new(std::env::current_exe()?);
    cmd.arg("serve").stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0000_0008 | 0x0000_0200 | 0x0800_0000); // détaché, nouveau groupe, sans fenêtre
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    cmd.spawn()?;
    Ok(())
}

// ---------- commandes ----------

fn run_or_direct(req: Value, timeout: Duration) -> Result<Value> {
    if let Some(r) = call_daemon(&req, timeout) {
        return Ok(r);
    }
    let _ = spawn_service(); // pour les appels suivants
    let mut engine = load_engine()?;
    Ok(handle(&mut engine, &req))
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let cwd = std::env::current_dir()?;
    let cwd_s = cwd.to_string_lossy().to_string();
    match args.first().map(String::as_str) {
        Some("texte") => {
            let mut text = String::new();
            std::io::stdin().read_to_string(&mut text)?;
            let r = run_or_direct(json!({"cmd": "texte", "text": text, "cwd": cwd_s}), Duration::from_secs(170))?;
            if let Some(e) = r["error"].as_str() {
                eprintln!("ERREUR : {e}");
                std::process::exit(1);
            }
            println!("{r}");
        }
        Some("doc") => {
            let files: Vec<String> = args[1..]
                .iter()
                .map(|f| std::fs::canonicalize(f).unwrap_or_else(|_| PathBuf::from(f)).to_string_lossy().trim_start_matches(r"\\?\").to_string())
                .collect();
            let r = run_or_direct(json!({"cmd": "doc", "files": files, "cwd": cwd_s}), Duration::from_secs(170))?;
            print!("{}", r["out"].as_str().unwrap_or(r["error"].as_str().unwrap_or("")));
            std::process::exit(r["rc"].as_i64().unwrap_or(1) as i32);
        }
        Some("restaurer") => {
            let engine = Engine::new(None, vec![])?;
            let table = load_table(&cwd);
            if table.is_empty() {
                println!("ERREUR : aucune table de correspondance pour ce dossier de travail.");
                std::process::exit(1);
            }
            for f in &args[1..] {
                let src = PathBuf::from(f);
                let text = std::fs::read_to_string(&src)?;
                let (out, unresolved) = engine.restore(&text, &table);
                let stem = src.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
                let stem = stem.strip_suffix(".anon").unwrap_or(&stem).to_string();
                let ext = src.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
                let dest = src.with_file_name(format!("{stem}.restaure{ext}"));
                std::fs::write(&dest, out)?;
                println!("OK {} -> {}", src.file_name().unwrap().to_string_lossy(), dest.display());
                if !unresolved.is_empty() {
                    println!("   ATTENTION placeholders non résolus : {}", unresolved.join(", "));
                }
            }
        }
        Some("table") => println!("{}", serde_json::to_string(&load_table(&cwd))?),
        Some("serve") => serve()?,
        Some("spawn") => spawn_service()?,
        Some("shutdown") => {
            call_daemon(&json!({"cmd": "shutdown"}), Duration::from_secs(5));
        }
        Some("ner") => {
            let mut text = String::new();
            std::io::stdin().read_to_string(&mut text)?;
            let mut n = ner::Ner::load(&model_dir(), ort_lib().as_deref())?;
            let v: Vec<Value> = n
                .predict(&text)?
                .iter()
                .map(|e| json!({"start": e.start, "end": e.end, "label": e.label, "text": &text[e.start..e.end]}))
                .collect();
            println!("{}", serde_json::to_string(&v)?);
        }
        Some("version") => println!("ano {}", env!("CARGO_PKG_VERSION")),
        _ => {
            eprintln!("usage : ano texte | doc FICHIER... | restaurer FICHIER... | table | spawn | serve | shutdown | ner");
            std::process::exit(2);
        }
    }
    Ok(())
}
