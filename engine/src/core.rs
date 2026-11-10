//! Cœur de l'anonymisation : règles, dictionnaire, fusion avec le NER, jetons, restauration.
//!
//! Port du prototype Python (`anonymiseur/core.py`) : mêmes règles, mêmes jetons
//! (`[PERSONNE_1]`, `[PERSONNE_2.1]`...). Les index sont des octets UTF-8.
use crate::ner::Ner;
use anyhow::Result;
use fancy_regex::Regex;
use indexmap::IndexMap;
use std::collections::HashMap;

/// Table jeton -> valeur d'origine, dans l'ordre d'insertion.
pub type Table = IndexMap<String, String>;

#[derive(Debug, Clone, Copy, PartialEq)]
enum Source {
    Rule, // règle déterministe ou dictionnaire
    Ner,
}

#[derive(Debug, Clone)]
struct Span {
    start: usize,
    end: usize,
    kind: String,
    source: Source,
}

impl Span {
    fn len(&self) -> usize {
        self.end - self.start
    }
}

/// Types du NER conservés : le contenu de santé d'un compte rendu n'est PAS masqué.
fn ner_kind(label: &str) -> Option<&'static str> {
    Some(match label {
        "PERSON_NAME" | "PERSON_ALIAS" => "PERSONNE",
        "POSTAL_ADDRESS" | "LOCATION" => "ADRESSE",
        "DATE_OF_BIRTH" => "DATE",
        "EMAIL_ADDRESS" => "EMAIL",
        "PHONE_NUMBER" => "TEL",
        "PERSON_IDENTIFIER" => "NIR",
        "DOCUMENT_IDENTIFIER" | "ACCOUNT_IDENTIFIER" => "DOSSIER",
        "BANK_ACCOUNT_IDENTIFIER" => "IBAN",
        _ => return None,
    })
}

/// Mots qui ne sont jamais des noms de personnes (titres, rôles).
const MOTS_VIDES: [&str; 13] = [
    "madame", "monsieur", "docteur", "mlle", "mme", "patient", "patiente", "infirmière",
    "infirmier", "fille", "fils", "dr", "mademoiselle",
];

pub struct Engine {
    rules: Vec<(&'static str, Regex)>,
    ph_re: Regex,
    titres: Regex,
    name_token: Regex,
    pub ner: Option<Ner>,
    /// dictionnaire de l'association : (type, valeurs en minuscules)
    dict: Vec<(String, Vec<String>)>,
}

impl Engine {
    pub fn new(ner: Option<Ner>, dict: Vec<(String, Vec<String>)>) -> Result<Self> {
        let titre = r"(?:Dr|Docteur|Pr|Professeur|Madame|Monsieur|Mme|Mlle|M\.?)(?:\s*/\s*(?:Madame|Monsieur|Mme|M\.?))?";
        Ok(Self {
            rules: vec![
                ("NIR", Regex::new(r"\b[12]\s?\d{2}\s?(?:0[1-9]|1[0-2])\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b")?),
                ("EMAIL", Regex::new(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")?),
                ("IBAN", Regex::new(r"\bFR\d{2}(?:\s?[0-9A-Z]{4}){5}\s?[0-9A-Z]{3}\b")?),
                ("TEL", Regex::new(r"(?<!\d)(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}(?!\d)")?),
                ("DATE", Regex::new(r"\b(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{2}\b")?),
                ("DOSSIER", Regex::new(r"\b[A-Z]{2,6}-\d{4}-\d{3,6}\b")?),
                ("ADRESSE", Regex::new(r"\b\d{5}\s+[A-ZÉÈ][\w'’-]+(?:[ -][A-ZÉÈa-zéèêëîïôûü'’-]+){0,3}")?),
            ],
            ph_re: Regex::new(r"\[([A-Z]+)_(\d+)(?:\.(\d+))?\]")?,
            titres: Regex::new(&format!(r"(?i)^(?:{titre}\s+)+"))?,
            name_token: Regex::new(r"[^\W\d_]{3,}(?:-[^\W\d_]+)*")?,
            ner,
            dict,
        })
    }

    fn detect_rules(&self, text: &str) -> Vec<Span> {
        let mut spans = Vec::new();
        for (kind, re) in &self.rules {
            for m in re.find_iter(text).flatten() {
                spans.push(Span { start: m.start(), end: m.end(), kind: kind.to_string(), source: Source::Rule });
            }
        }
        // dictionnaire : mots entiers, insensible à la casse
        let lower = text.to_lowercase();
        if lower.len() == text.len() {
            for (kind, values) in &self.dict {
                for v in values.iter().filter(|v| !v.is_empty()) {
                    let mut from = 0;
                    while let Some(pos) = lower[from..].find(v.as_str()) {
                        let (s, e) = (from + pos, from + pos + v.len());
                        let before_ok = lower[..s].chars().next_back().map_or(true, |c| !c.is_alphanumeric());
                        let after_ok = lower[e..].chars().next().map_or(true, |c| !c.is_alphanumeric());
                        if before_ok && after_ok {
                            spans.push(Span { start: s, end: e, kind: kind.clone(), source: Source::Rule });
                        }
                        from = e;
                    }
                }
            }
        }
        spans
    }

    fn detect_ner(&mut self, text: &str) -> Result<Vec<Span>> {
        let Some(ner) = self.ner.as_mut() else { return Ok(vec![]) };
        Ok(ner
            .predict(text)?
            .into_iter()
            .filter_map(|e| {
                ner_kind(&e.label).map(|k| Span { start: e.start, end: e.end, kind: k.into(), source: Source::Ner })
            })
            .collect())
    }

    /// Résout les chevauchements : même type = union ; sinon la règle prime, puis le plus long.
    fn merge(mut spans: Vec<Span>) -> Vec<Span> {
        spans.sort_by(|a, b| a.start.cmp(&b.start).then(b.len().cmp(&a.len())));
        let mut out: Vec<Span> = Vec::new();
        for s in spans {
            if let Some(prev) = out.last_mut() {
                if s.start < prev.end {
                    if s.kind == prev.kind {
                        prev.end = prev.end.max(s.end);
                        prev.source = if s.source == Source::Rule || prev.source == Source::Rule {
                            Source::Rule
                        } else {
                            s.source
                        };
                    } else if (s.source == Source::Rule && prev.source != Source::Rule)
                        || (s.source == prev.source && s.len() > prev.len())
                    {
                        *prev = s;
                    }
                    continue;
                }
            }
            out.push(s);
        }
        out
    }

    /// Faux positifs du NER sur du texte technique (chemins, identifiants à chiffres...).
    fn looks_technical(text: &str, s: &Span) -> bool {
        let value = &text[s.start..s.end];
        let before = text[..s.start].chars().next_back().unwrap_or(' ');
        let after = text[s.end..].chars().next().unwrap_or(' ');
        value.chars().any(|c| c.is_numeric() || c == '_' || c == '@' || c == '\\')
            || "/\\".contains(before)
            || "/\\".contains(after)
    }

    fn clean(&self, value: &str) -> String {
        let v = value.trim_matches(|c| " ,.;:()".contains(c));
        self.titres.replace(v, "").to_string()
    }

    /// Anonymise `text` avec (et en mettant à jour) la table de l'espace de travail.
    pub fn anonymize(&mut self, text: &str, table: &mut Table) -> Result<String> {
        // les jetons déjà présents sont intouchables (idempotence)
        let ph_ranges: Vec<(usize, usize)> =
            self.ph_re.find_iter(text).flatten().map(|m| (m.start(), m.end())).collect();
        let touches = |s: &Span| ph_ranges.iter().any(|&(a, b)| s.start < b && a < s.end);

        let mut spans = self.detect_rules(text);
        spans.extend(self.detect_ner(text)?);
        spans.retain(|s| !touches(s));
        let mut spans = Self::merge(spans);
        spans.retain(|s| !(s.source == Source::Ner && s.kind == "PERSONNE" && Self::looks_technical(text, s)));

        // reprise de la table existante
        let mut by_norm: HashMap<(String, String), String> = HashMap::new();
        let mut counters: HashMap<String, usize> = HashMap::new();
        for (ph, value) in table.iter() {
            if let Ok(Some(c)) = self.ph_re.captures(ph) {
                let kind = c.get(1).unwrap().as_str().to_string();
                by_norm.insert((kind.clone(), value.to_lowercase()), ph.clone());
                if c.get(3).is_none() {
                    let n: usize = c.get(2).unwrap().as_str().parse().unwrap_or(0);
                    let e = counters.entry(kind).or_insert(0);
                    *e = (*e).max(n);
                }
            }
        }

        // 1) un jeton par valeur (même valeur -> même jeton)
        for s in &spans {
            let mut value = text[s.start..s.end].to_string();
            if matches!(s.kind.as_str(), "PERSONNE" | "ADRESSE" | "LIEU") {
                value = self.clean(&value);
                if value.chars().count() < 2 {
                    continue;
                }
                // un titre ou un rôle seul (« Madame », « infirmière ») n'est pas un nom
                if s.kind == "PERSONNE" && MOTS_VIDES.contains(&value.to_lowercase().as_str()) {
                    continue;
                }
            }
            let key = (s.kind.clone(), value.to_lowercase());
            if !by_norm.contains_key(&key) {
                let n = counters.entry(s.kind.clone()).or_insert(0);
                *n += 1;
                let ph = format!("[{}_{}]", s.kind, n);
                by_norm.insert(key, ph.clone());
                table.insert(ph, value);
            }
        }

        // 2) propagation : chaque mot d'un nom reçoit un jeton dérivé [PERSONNE_2.1]
        let snapshot: Vec<(String, String)> = table.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        let vides = MOTS_VIDES;
        for (ph, value) in snapshot {
            if ph.starts_with("[PERSONNE_") && !ph.contains('.') {
                let toks: Vec<String> = self.name_token.find_iter(&value).flatten().map(|m| m.as_str().to_string()).collect();
                for (i, tok) in toks.iter().enumerate() {
                    let key = ("PERSONNE".to_string(), tok.to_lowercase());
                    if vides.contains(&tok.to_lowercase().as_str()) || by_norm.contains_key(&key) {
                        continue;
                    }
                    let alias = format!("{}.{}]", &ph[..ph.len() - 1], i + 1);
                    by_norm.insert(key, alias.clone());
                    table.insert(alias, tok.clone());
                }
            }
        }

        // 3) remplacement global (valeurs longues d'abord, insensible à la casse),
        //    sans toucher aux jetons existants
        let mut lookup: HashMap<String, String> = HashMap::new();
        for ((_, v), ph) in &by_norm {
            lookup.insert(v.clone(), ph.clone());
        }
        if lookup.is_empty() {
            return Ok(text.to_string());
        }
        let mut ordered: Vec<&String> = lookup.keys().collect();
        ordered.sort_by(|a, b| b.len().cmp(&a.len()).then(a.cmp(b)));
        let alternation = ordered.iter().map(|v| fancy_regex::escape(v).to_string()).collect::<Vec<_>>().join("|");
        let pattern = Regex::new(&format!(r"(?i)(?<!\w)({alternation})(?!\w)"))?;
        let sub = |segment: &str| -> String {
            let mut out = String::new();
            let mut last = 0;
            for m in pattern.find_iter(segment).flatten() {
                out.push_str(&segment[last..m.start()]);
                match lookup.get(&m.as_str().to_lowercase()) {
                    Some(ph) => out.push_str(ph),
                    None => out.push_str(m.as_str()),
                }
                last = m.end();
            }
            out.push_str(&segment[last..]);
            out
        };
        let mut result = String::new();
        let mut pos = 0;
        for &(a, b) in &ph_ranges {
            result.push_str(&sub(&text[pos..a]));
            result.push_str(&text[a..b]);
            pos = b;
        }
        result.push_str(&sub(&text[pos..]));
        Ok(result)
    }

    /// Remet les valeurs d'origine ; renvoie aussi les jetons restés non résolus.
    pub fn restore(&self, text: &str, table: &Table) -> (String, Vec<String>) {
        let mut keys: Vec<&String> = table.keys().collect();
        keys.sort_by(|a, b| b.len().cmp(&a.len()));
        let mut out = text.to_string();
        for k in keys {
            out = out.replace(k.as_str(), &table[k]);
        }
        let unresolved: Vec<String> = self
            .ph_re
            .find_iter(&out)
            .flatten()
            .map(|m| m.as_str().to_string())
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .collect();
        (out, unresolved)
    }

    /// Nombre de données sensibles par type (jamais les valeurs).
    pub fn count_kinds(&mut self, text: &str) -> Result<std::collections::BTreeMap<String, usize>> {
        let mut table = Table::new();
        self.anonymize(text, &mut table)?;
        let mut counts = std::collections::BTreeMap::new();
        for ph in table.keys().filter(|p| !p.contains('.')) {
            if let Ok(Some(c)) = self.ph_re.captures(ph) {
                *counts.entry(c.get(1).unwrap().as_str().to_string()).or_insert(0) += 1;
            }
        }
        Ok(counts)
    }

    pub fn placeholder_kinds(&self, text: &str) -> std::collections::BTreeMap<String, usize> {
        let mut used = std::collections::BTreeMap::new();
        for m in self.ph_re.captures_iter(text).flatten() {
            *used.entry(m.get(1).unwrap().as_str().to_string()).or_insert(0) += 1;
        }
        used
    }
}
