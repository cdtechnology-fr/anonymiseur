//! Extraction du texte des documents : txt, md, csv, json, html, docx, pdf.
use anyhow::{bail, Context, Result};
use quick_xml::events::Event;
use quick_xml::Reader;
use std::io::Read;
use std::path::Path;

pub fn extract_text(path: &Path) -> Result<String> {
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase();
    match ext.as_str() {
        "txt" | "md" | "csv" | "json" | "html" | "htm" => {
            let bytes = std::fs::read(path).with_context(|| format!("lecture de {}", path.display()))?;
            Ok(String::from_utf8_lossy(&bytes).to_string())
        }
        "docx" => docx_text(path),
        "pdf" => {
            let text = pdf_extract::extract_text(path).map_err(|e| anyhow::anyhow!("PDF illisible : {e}"))?;
            if text.trim().chars().count() < 20 {
                bail!("PDF sans texte exploitable (probablement un scan) : l'OCR n'est pas disponible, le document ne peut pas être traité automatiquement.");
            }
            Ok(text)
        }
        _ => bail!("Format non pris en charge : .{ext} (formats gérés : txt, md, csv, pdf, docx)."),
    }
}

fn docx_text(path: &Path) -> Result<String> {
    let file = std::fs::File::open(path).with_context(|| format!("ouverture de {}", path.display()))?;
    let mut zip = zip::ZipArchive::new(file).context("fichier docx invalide")?;
    let mut xml = String::new();
    zip.by_name("word/document.xml")
        .context("word/document.xml absent")?
        .read_to_string(&mut xml)?;
    let mut reader = Reader::from_str(&xml);
    let mut out = String::new();
    let mut in_text = false;
    loop {
        match reader.read_event() {
            Ok(Event::Start(e)) if e.name().as_ref() == "w:t" => in_text = true,
            Ok(Event::End(e)) => match e.name().as_ref() {
                "w:t" => in_text = false,
                "w:p" => out.push('\n'),
                "w:tc" => out.push_str(" | "),
                _ => {}
            },
            Ok(Event::Empty(e)) => match e.name().as_ref() {
                "w:tab" => out.push('\t'),
                "w:br" => out.push('\n'),
                _ => {}
            },
            Ok(Event::Text(t)) if in_text => out.push_str(&t.xml10_content()),
            Ok(Event::Eof) => break,
            Err(e) => bail!("docx illisible : {e}"),
            _ => {}
        }
    }
    Ok(out)
}
