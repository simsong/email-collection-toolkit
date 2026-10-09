// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Compile the shared search language into bound SQLite predicates.
// Calendar dates use the same worldwide 50-hour interval as the Python reader.
// Address selectors include derived header names without rewriting catalog data.
// Attachment terms may match across message and attachment indexes independently.
// Folder selections retain the existing versioned base64 JSON interchange format.
// Staged and complete search paths consume one plan so their results agree.
use anyhow::{bail, ensure, Context, Result};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{Datelike, Duration, NaiveDate};
use rusqlite::types::Value as SqlValue;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy)]
pub(crate) enum Filter {
    All,
    Hash,
    Membership,
    Sender,
    Date,
}
pub(crate) struct Plan {
    pub clauses: Vec<String>,
    pub values: Vec<SqlValue>,
    pub terms: Vec<String>,
    pub filter: Filter,
}

pub(crate) fn folded(value: &str) -> String {
    caseless::default_case_fold_str(value)
}
pub(crate) fn subject_predicate(value: &str) -> (String, SqlValue) {
    ("m.message_pk IN(SELECT subject_match.message_pk FROM messages subject_match INDEXED BY messages_subject_message WHERE lower(subject_match.subject) LIKE ? ESCAPE '\\')".into(), contains(value).into())
}

#[derive(Clone, Deserialize, Serialize)]
pub(crate) struct Selection {
    pub version: u8,
    pub path: String,
    pub volume_identity: Option<String>,
}
impl Selection {
    pub fn decode(token: &str) -> Result<Self> {
        let selection: Self = serde_json::from_slice(&URL_SAFE_NO_PAD.decode(token)?)?;
        ensure!(
            selection.version == 1,
            "Unsupported folder selection version"
        );
        ensure!(
            !selection.path.starts_with('/')
                && !selection.path.split('/').any(|v| matches!(v, "." | "..")),
            "Invalid folder selection path"
        );
        Ok(selection)
    }
    pub fn token(&self) -> Result<String> {
        Ok(URL_SAFE_NO_PAD.encode(serde_json::to_vec(self)?))
    }
}
pub(crate) fn date_bounds(value: &str) -> Result<(String, String)> {
    let value = value.trim();
    let digits = |part: &str, min: usize, max: usize| {
        (min..=max).contains(&part.len()) && part.bytes().all(|b| b.is_ascii_digit())
    };
    let iso: Vec<_> = value.split('-').collect();
    let slash: Vec<_> = value.split('/').collect();
    let words: Vec<_> = value.split_whitespace().collect();
    let valid =
        (iso.len() == 3 && digits(iso[0], 4, 4) && digits(iso[1], 2, 2) && digits(iso[2], 2, 2))
            || (slash.len() == 3
                && digits(slash[0], 1, 2)
                && digits(slash[1], 1, 2)
                && digits(slash[2], 4, 4))
            || (words.len() == 3
                && words[0].bytes().all(|b| b.is_ascii_alphabetic())
                && digits(words[1].strip_suffix(',').unwrap_or(words[1]), 1, 2)
                && digits(words[2], 4, 4));
    ensure!(
        valid,
        "Invalid calendar date; use YYYY-MM-DD, M/D/YYYY, or Month D, YYYY"
    );
    let date = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%B %d %Y",
        "%b %d %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ]
    .iter()
    .find_map(|format| NaiveDate::parse_from_str(value, format).ok())
    .context("Invalid calendar date; use YYYY-MM-DD, M/D/YYYY, or Month D, YYYY")?;
    ensure!((1..=9999).contains(&date.year()), "Date out of range");
    let midnight = date.and_hms_opt(0, 0, 0).context("Invalid date")?;
    let start = midnight
        .checked_sub_signed(Duration::hours(14))
        .context("Date out of range")?;
    let end = midnight
        .checked_add_signed(Duration::hours(36))
        .context("Date out of range")?;
    ensure!(
        (1..=9999).contains(&start.year()) && (1..=9999).contains(&end.year()),
        "Date out of range"
    );
    Ok((
        start.format("%Y-%m-%dT%H:%M:%S+00:00").to_string(),
        end.format("%Y-%m-%dT%H:%M:%S+00:00").to_string(),
    ))
}
pub(crate) fn normalized_date(value: &str) -> Result<String> {
    let (start, _) = date_bounds(value)?;
    let start = chrono::DateTime::parse_from_rfc3339(&start)?;
    Ok((start + Duration::hours(14)).format("%Y-%m-%d").to_string())
}
pub(crate) fn quoted(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('\"', "\\\""))
}
pub(crate) fn contains(value: &str) -> String {
    format!(
        "%{}%",
        value
            .replace('\\', "\\\\")
            .replace('%', "\\%")
            .replace('_', "\\_")
    )
}
pub(crate) fn plan(query: &str, attachments: bool, selections: Option<&Value>) -> Result<Plan> {
    let mut tokens = Vec::new();
    let mut token = String::new();
    let (mut quoted, mut escaped, mut started) = (None, false, false);
    for ch in query.chars() {
        if escaped {
            // POSIX shlex keeps backslashes before ordinary characters in double quotes.
            if quoted == Some('"') && !matches!(ch, '"' | '\\') {
                token.push('\\');
            }
            token.push(ch);
            escaped = false;
        } else if ch == '\\' && quoted != Some('\'') {
            started = true;
            escaped = true;
        } else if quoted == Some(ch) {
            quoted = None;
        } else if quoted.is_none() && matches!(ch, '\'' | '"') {
            started = true;
            quoted = Some(ch);
        } else if ch.is_whitespace() && quoted.is_none() {
            if started {
                tokens.push(std::mem::take(&mut token));
                started = false;
            }
        } else {
            started = true;
            token.push(ch);
        }
    }
    ensure!(
        quoted.is_none() && !escaped,
        "Close the quoted search phrase"
    );
    if started {
        tokens.push(token);
    }
    let mut clauses = vec!["m.category IN ('Archive','Sent')".to_string()];
    let mut values = Vec::new();
    let mut terms = Vec::new();
    let mut fulltext = Vec::new();
    let mut text_terms = Vec::new();
    let mut selector_terms = [const { Vec::<String>::new() }; 6];
    let (mut membership, mut sender, mut dates) = (false, false, false);
    for token in tokens {
        if let Some((field, value)) = token.split_once(':').filter(|(field, _)| {
            [
                "date", "before", "after", "subject", "from", "to", "cc", "bcc", "any",
            ]
            .contains(&field.to_ascii_lowercase().as_str())
        }) {
            let field = field.to_ascii_lowercase();
            ensure!(!value.is_empty(), "Enter a value after {field}:");
            let value = folded(value.trim());
            ensure!(!value.is_empty(), "Enter a value after {field}:");
            match field.as_str() {
                "date" | "before" | "after" => {
                    dates = true;
                    let (start, end) = date_bounds(&value)?;
                    match field.as_str() {
                        "date" => {
                            clauses.push("m.date_utc >= ? AND m.date_utc < ?".into());
                            values.extend([start.into(), end.into()]);
                        }
                        "before" => {
                            clauses.push("m.date_utc < ?".into());
                            values.push(start.into());
                        }
                        _ => {
                            clauses.push("m.date_utc >= ?".into());
                            values.push(end.into());
                        }
                    }
                    continue;
                }
                "subject" => {
                    membership = true;
                    let (clause, value) = subject_predicate(&value);
                    clauses.push(clause);
                    values.push(value);
                }
                "from" | "to" | "cc" | "bcc" | "any" => {
                    membership |= field != "from";
                    sender |= field == "from";
                    let matching = "SELECT address_pk FROM email_addresses WHERE lower(address) LIKE ? ESCAPE '\\' UNION SELECT a.address_pk FROM address_search_names n JOIN email_addresses a ON a.address=n.address WHERE lower(n.name) LIKE ? ESCAPE '\\'";
                    let address = match field.as_str() {
                        "from" => format!("m.sender_address_pk IN ({matching})"),
                        "any" => format!("m.message_pk IN (WITH matching AS MATERIALIZED ({matching}) SELECT sent.message_pk FROM matching CROSS JOIN messages sent INDEXED BY messages_sender_address_pk ON sent.sender_address_pk=matching.address_pk UNION SELECT r.message_pk FROM matching CROSS JOIN recipients r INDEXED BY recipients_address_pk ON r.address_pk=matching.address_pk)"),
                        _ => format!("m.message_pk IN (WITH matching AS MATERIALIZED ({matching}) SELECT r.message_pk FROM matching CROSS JOIN recipients r INDEXED BY recipients_address_pk ON r.address_pk=matching.address_pk WHERE r.role=?)"),
                    };
                    clauses.push(address);
                    values.extend([contains(&value).into(), contains(&value).into()]);
                    if !matches!(field.as_str(), "from" | "any") {
                        values.push(field.clone().into());
                    }
                }
                _ => bail!("Unknown search selector: {field}:"),
            }
            let index = ["any", "from", "to", "cc", "bcc", "subject"]
                .iter()
                .position(|f| *f == field)
                .unwrap();
            selector_terms[index].push(value);
        } else {
            fulltext.push(format!("\"{}\"", token.replace('"', "")));
            text_terms.push(token);
        }
    }
    let has_text = !fulltext.is_empty();
    if attachments {
        for term in fulltext {
            clauses.push("m.sha256 IN(SELECT sha256 FROM search.message_fts WHERE message_fts MATCH ? UNION SELECT sha256 FROM search.attachment_fts WHERE attachment_fts MATCH ?)".into());
            values.extend([term.clone().into(), term.into()]);
        }
    } else if !fulltext.is_empty() {
        clauses.push(
            "m.sha256 IN(SELECT sha256 FROM search.message_fts WHERE message_fts MATCH ?)".into(),
        );
        values.push(fulltext.join(" AND ").into());
    }
    if let Some(selections) = selections {
        let selections = selections.as_array().context("Invalid folder selections")?;
        ensure!(selections.len() <= 500, "Too many selected folders");
        let mut alternatives = Vec::new();
        for token in selections {
            let selection = Selection::decode(token.as_str().context("Invalid folder selection")?)?;
            let mut conditions = vec!["1".to_string()];
            let index = if selection.volume_identity.is_some() {
                "source_files_volume_hierarchy"
            } else {
                "source_files_hierarchy_volume"
            };
            if let Some(volume) = selection.volume_identity {
                conditions.push("s.source_volume_pk IN (SELECT source_volume_pk FROM source_volumes WHERE identity_json=?)".into());
                values.push(volume.into());
            }
            if !selection.path.is_empty() {
                conditions.push(
                    "(s.hierarchy_path=? OR (s.hierarchy_path>=? AND s.hierarchy_path<?))".into(),
                );
                values.extend([
                    selection.path.clone().into(),
                    format!("{}/", selection.path).into(),
                    format!("{}0", selection.path).into(),
                ]);
            }
            alternatives.push(format!("SELECT o.message_pk FROM source_files s INDEXED BY {index} CROSS JOIN observations o INDEXED BY observations_source_file_offset USING(source_file_pk) WHERE {}", conditions.join(" AND ")));
        }
        if !alternatives.is_empty() {
            membership = true;
            clauses.push(format!(
                "m.message_pk IN ({})",
                alternatives.join(" UNION ")
            ));
        }
    }
    let mut seen = std::collections::HashSet::new();
    for term in text_terms
        .into_iter()
        .chain(selector_terms.into_iter().flatten())
    {
        let key = folded(&term);
        if !key.is_empty() && seen.insert(key) {
            terms.push(term);
        }
    }
    let filter = if has_text {
        Filter::Hash
    } else if membership {
        Filter::Membership
    } else if sender {
        Filter::Sender
    } else if dates {
        Filter::Date
    } else {
        Filter::All
    };
    Ok(Plan {
        clauses,
        values,
        terms,
        filter,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn worldwide_dates_and_query_quoting() {
        // Worldwide date requirement: same interval regardless of UI locale or format.
        for input in ["2020-01-05", "1/5/2020", "January 5, 2020", "Jan 5 2020"] {
            assert_eq!(
                date_bounds(input).unwrap(),
                (
                    "2020-01-04T10:00:00+00:00".into(),
                    "2020-01-06T12:00:00+00:00".into()
                )
            );
        }
        assert!(date_bounds("2023-02-29").is_err());
        assert!(date_bounds("2024-02-29").is_ok());
        // Shared selector syntax rejects punctuation embedded in numeric dates.
        for input in [
            "1,2/3/2020",
            "2020,-01-05",
            "January 5,, 2020",
            "Jan, 5 2020",
        ] {
            assert!(date_bounds(input).is_err(), "{input}");
            assert!(plan(&format!("date:\"{input}\""), false, None).is_err());
        }
        assert_eq!(
            date_bounds("2020-01-01").unwrap().0,
            "2019-12-31T10:00:00+00:00"
        );
        let parsed = plan(r#"subject:"say \"hello\"""#, false, None).unwrap();
        assert_eq!(parsed.values, [SqlValue::Text("%say \"hello\"%".into())]);
        let parsed = plan("subject:'annual report' ticket:123", false, None).unwrap();
        assert_eq!(parsed.terms, ["ticket:123", "annual report"]);
        assert_eq!(
            parsed.values,
            [
                SqlValue::Text("%annual report%".into()),
                SqlValue::Text("\"ticket:123\"".into())
            ]
        );
    }
}
