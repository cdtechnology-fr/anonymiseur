//! Détecteur NER : modèle bardsai/eu-pii-anonimization-multilang-v3 (ONNX int8).
//!
//! Port du décodage de référence (`pii_detector.py` du modèle) : fenêtres de 512 tokens
//! (recouvrement 128), probabilités moyennées par mot, fusion des mots adjacents de
//! même type, contrôles de format. Tous les index sont des **octets** du texte UTF-8.
use anyhow::{Context, Result};
use fancy_regex::Regex;
use ndarray::Array2;
use ort::session::Session;
use ort::value::Tensor;
use std::collections::BTreeMap;
use std::path::Path;
use tokenizers::{Tokenizer, TruncationDirection, TruncationParams, TruncationStrategy};

const MAX_LEN: usize = 512;
const STRIDE: usize = 128;
const ID_TYPES: [&str; 6] = [
    "PERSON_IDENTIFIER",
    "BANK_ACCOUNT_IDENTIFIER",
    "PAYMENT_CARD",
    "ACCOUNT_IDENTIFIER",
    "DOCUMENT_IDENTIFIER",
    "VEHICLE_IDENTIFIER",
];

/// Entité détectée : plage d'octets et type du modèle (ex. `PERSON_NAME`).
#[derive(Debug, Clone, PartialEq)]
pub struct Entity {
    pub start: usize,
    pub end: usize,
    pub label: String,
}

pub struct Ner {
    session: Session,
    tokenizer: Tokenizer,
    /// types triés ; le dernier index (`types.len()`) représente « O » (rien)
    types: Vec<String>,
    /// pour chaque étiquette du modèle : index de type, et si elle commence une entité
    label_type: Vec<usize>,
    label_is_b: Vec<bool>,
    word_re: Regex,
    email_re: Regex,
    urlish_re: Regex,
}

impl Ner {
    /// `dir` contient `model_quantized.onnx`, `tokenizer.json` et `config.json`.
    pub fn load(dir: &Path, ort_lib: Option<&Path>) -> Result<Self> {
        if let Some(lib) = ort_lib {
            ort::init_from(lib.to_string_lossy().to_string()).commit()?;
        }
        let session = Session::builder()?
            .with_intra_threads(4)?
            .commit_from_file(dir.join("model_quantized.onnx"))
            .context("chargement du modèle ONNX")?;
        let mut tokenizer = Tokenizer::from_file(dir.join("tokenizer.json"))
            .map_err(|e| anyhow::anyhow!("tokenizer: {e}"))?;
        tokenizer
            .with_truncation(Some(TruncationParams {
                max_length: MAX_LEN,
                stride: STRIDE,
                strategy: TruncationStrategy::LongestFirst,
                direction: TruncationDirection::Right,
            }))
            .map_err(|e| anyhow::anyhow!("troncature: {e}"))?;
        tokenizer.with_padding(None);

        let cfg: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(dir.join("config.json"))?)?;
        let id2label: BTreeMap<usize, String> = cfg["id2label"]
            .as_object()
            .context("config.json sans id2label")?
            .iter()
            .map(|(k, v)| (k.parse().unwrap(), v.as_str().unwrap().to_string()))
            .collect();
        let mut types: Vec<String> = id2label
            .values()
            .filter(|l| l.as_str() != "O")
            .map(|l| l.split_once('-').unwrap().1.to_string())
            .collect();
        types.sort();
        types.dedup();
        let o_index = types.len();
        let mut label_type = Vec::new();
        let mut label_is_b = Vec::new();
        for (_, lab) in id2label.iter() {
            if lab == "O" {
                label_type.push(o_index);
                label_is_b.push(false);
            } else {
                let (prefix, t) = lab.split_once('-').unwrap();
                label_type.push(types.iter().position(|x| x == t).unwrap());
                label_is_b.push(prefix == "B" || prefix == "S");
            }
        }
        Ok(Self {
            session,
            tokenizer,
            types,
            label_type,
            label_is_b,
            word_re: Regex::new(r#"[^\s,;|"<>()\[\]{}*^]+"#)?,
            email_re: Regex::new(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?![\w-])")?,
            urlish_re: Regex::new(r"(?i)(https?://|www\.|\w\.[a-z]{2,}\b|/\w)")?,
        })
    }

    /// Probabilités par type, moyennées sur les fenêtres, positionnées au début de chaque token.
    fn char_probs(&mut self, text: &str) -> Result<(Vec<Vec<f32>>, Vec<f32>, Vec<f32>)> {
        let nt = self.types.len() + 1;
        let n = text.len();
        let mut acc = vec![vec![0f32; nt]; n + 1];
        let mut cnt = vec![0f32; n + 1];
        let mut bmass = vec![0f32; n + 1];

        let enc = self
            .tokenizer
            .encode(text, true)
            .map_err(|e| anyhow::anyhow!("encodage: {e}"))?;
        let mut windows = vec![enc.clone()];
        windows.extend(enc.get_overflowing().iter().cloned());

        for w in &windows {
            let ids: Vec<i64> = w.get_ids().iter().map(|&x| x as i64).collect();
            let mask: Vec<i64> = w.get_attention_mask().iter().map(|&x| x as i64).collect();
            let len = ids.len();
            let ids_t = Tensor::from_array(Array2::from_shape_vec((1, len), ids)?)?;
            let mask_t = Tensor::from_array(Array2::from_shape_vec((1, len), mask)?)?;
            let outputs = self
                .session
                .run(ort::inputs!["input_ids" => ids_t, "attention_mask" => mask_t])?;
            let (_shape, logits) = outputs[0].try_extract_tensor::<f32>()?; // [1, len, n_labels] à plat
            let n_labels = self.label_type.len();
            for (j, &(s, e)) in w.get_offsets().iter().enumerate() {
                if e <= s || w.get_attention_mask()[j] == 0 {
                    continue;
                }
                // softmax du token j
                let row: Vec<f32> = logits[j * n_labels..(j + 1) * n_labels].to_vec();
                let mx = row.iter().cloned().fold(f32::MIN, f32::max);
                let exps: Vec<f32> = row.iter().map(|v| (v - mx).exp()).collect();
                let sum: f32 = exps.iter().sum();
                // les offsets BPE incluent l'espace initial : on avance au premier non-blanc
                let mut s = s;
                while s + 1 < e {
                    let c = text[s..].chars().next().unwrap();
                    if c.is_whitespace() && s + c.len_utf8() < e {
                        s += c.len_utf8();
                    } else {
                        break;
                    }
                }
                for (k, ex) in exps.iter().enumerate() {
                    let p = ex / sum;
                    acc[s][self.label_type[k]] += p;
                    if self.label_is_b[k] {
                        bmass[s] += p;
                    }
                }
                cnt[s] += 1.0;
            }
        }
        Ok((acc, cnt, bmass))
    }

    fn valid(&self, text: &str, start: usize, end: usize, label: &str) -> bool {
        let v = &text[start..end];
        let digits = v.chars().filter(|c| c.is_ascii_digit() || c.is_numeric()).count();
        match label {
            "IDENTIFYING_LINK" => self.urlish_re.is_match(v).unwrap_or(false),
            "PHONE_NUMBER" => digits >= 5,
            l if ID_TYPES.contains(&l) => digits >= 2,
            _ => true,
        }
    }

    pub fn predict(&mut self, text: &str) -> Result<Vec<Entity>> {
        if text.trim().is_empty() {
            return Ok(vec![]);
        }
        let (acc, cnt, bmass) = self.char_probs(text)?;
        let o_index = self.types.len();
        let mut spans: Vec<(usize, usize, usize)> = Vec::new(); // début, fin, index de type

        for m in self.word_re.find_iter(text) {
            let m = m?;
            let (mut ws, mut we) = (m.start(), m.end());
            while ws < we {
                let c = text[ws..].chars().next().unwrap();
                if "([{<\"'«„".contains(c) {
                    ws += c.len_utf8();
                } else {
                    break;
                }
            }
            while we > ws {
                let c = text[..we].chars().next_back().unwrap();
                if ")]}>\"'».,;:!?".contains(c) {
                    we -= c.len_utf8();
                } else {
                    break;
                }
            }
            if we <= ws {
                continue;
            }
            let sel: Vec<usize> = (ws..we).filter(|&i| cnt[i] > 0.0).collect();
            if sel.is_empty() {
                continue;
            }
            let total: f32 = sel.iter().map(|&i| cnt[i]).sum();
            let mut best = (0usize, f32::MIN);
            for t in 0..=o_index {
                let v: f32 = sel.iter().map(|&i| acc[i][t]).sum::<f32>() / total;
                if v > best.1 {
                    best = (t, v);
                }
            }
            if best.0 == o_index {
                continue;
            }
            let first = sel[0];
            let startb = bmass[first] / cnt[first].max(1.0);
            let t = best.0;
            if let Some(prev) = spans.last_mut() {
                let gap = &text[prev.1..ws];
                if prev.2 == t
                    && gap.trim_matches(|c| " ,-/.()".contains(c)).is_empty()
                    && !gap.contains("\n\n")
                    && startb < 0.5
                {
                    prev.1 = we;
                    continue;
                }
            }
            spans.push((ws, we, t));
        }

        let mut out: Vec<Entity> = spans
            .into_iter()
            .map(|(s, e, t)| Entity { start: s, end: e, label: self.types[t].clone() })
            .filter(|e| self.valid(text, e.start, e.end, &e.label))
            .collect();

        // toute adresse e-mail trouvée par règle est une entité EMAIL_ADDRESS
        let mut emails = Vec::new();
        for m in self.email_re.find_iter(text) {
            let m = m?;
            emails.push((m.start(), m.end()));
        }
        if !emails.is_empty() {
            out.retain(|e| !emails.iter().any(|&(a, b)| e.start < b && a < e.end));
            out.extend(emails.into_iter().map(|(a, b)| Entity {
                start: a,
                end: b,
                label: "EMAIL_ADDRESS".into(),
            }));
        }
        out.sort_by_key(|e| e.start);
        Ok(out)
    }
}
