// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Compile the shared search language into bound SQLite predicates.
// Calendar dates use the same worldwide 50-hour interval as the Python reader.
// Address selectors include derived header names without rewriting catalog data.
// Attachment terms may match across message and attachment indexes independently.
// Folder selections retain the existing versioned base64 JSON interchange format.
// Staged and complete search paths consume one plan so their results agree.
use anyhow::{bail, ensure, Context, Result};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{Duration, NaiveDate};
use rusqlite::types::Value as SqlValue;
use serde::{Deserialize, Serialize};
use serde_json::Value;

type Plan = (Vec<String>, Vec<SqlValue>, Vec<String>);

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
    let value = value.replace(',', "");
    let date = ["%Y-%m-%d", "%m/%d/%Y", "%B %d %Y", "%b %d %Y"]
        .iter()
        .find_map(|format| NaiveDate::parse_from_str(&value, format).ok())
        .context("Invalid calendar date; use YYYY-MM-DD, M/D/YYYY, or Month D, YYYY")?;
    let midnight = date.and_hms_opt(0, 0, 0).context("Invalid date")?;
    let start = midnight
        .checked_sub_signed(Duration::hours(14))
        .context("Date out of range")?;
    let end = midnight
        .checked_add_signed(Duration::hours(36))
        .context("Date out of range")?;
    Ok((
        start.format("%Y-%m-%dT%H:%M:%S+00:00").to_string(),
        end.format("%Y-%m-%dT%H:%M:%S+00:00").to_string(),
    ))
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
pub(crate) fn query_parts(query: &str) -> Result<Plan> {
    plan(query, false, None)
}
pub(crate) fn plan(query: &str, attachments: bool, selections: Option<&Value>) -> Result<Plan> {
    let mut tokens = Vec::new();
    let mut token = String::new();
    let (mut quoted, mut escaped) = (None, false);
    for ch in query.chars() {
        if escaped {
            token.push(ch);
            escaped = false;
        } else if ch == '\\' && quoted != Some('\'') {
            escaped = true;
        } else if quoted == Some(ch) {
            quoted = None;
        } else if quoted.is_none() && matches!(ch, '\'' | '"') {
            quoted = Some(ch);
        } else if ch.is_whitespace() && quoted.is_none() {
            if !token.is_empty() {
                tokens.push(std::mem::take(&mut token));
            }
        } else {
            token.push(ch);
        }
    }
    ensure!(
        quoted.is_none() && !escaped,
        "Close the quoted search phrase"
    );
    if !token.is_empty() {
        tokens.push(token);
    }
    let mut clauses = vec!["m.category IN ('Archive','Sent')".to_string()];
    let mut values = Vec::new();
    let mut terms = Vec::new();
    let mut fulltext = Vec::new();
    for token in tokens {
        if let Some((field, value)) = token.split_once(':').filter(|(field, _)| {
            [
                "date", "before", "after", "subject", "from", "to", "cc", "bcc", "any",
            ]
            .contains(&field.to_ascii_lowercase().as_str())
        }) {
            let field = field.to_ascii_lowercase();
            ensure!(!value.is_empty(), "Enter a value after {field}:");
            match field.as_str() {
                "date" | "before" | "after" => {
                    let (start, end) = date_bounds(value)?;
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
                    clauses.push("m.subject LIKE ? ESCAPE '\\'".into());
                    values.push(contains(value).into());
                }
                "from" | "to" | "cc" | "bcc" | "any" => {
                    let matching = "SELECT address_pk FROM email_addresses WHERE address LIKE ? ESCAPE '\\' UNION SELECT a.address_pk FROM address_search_names n JOIN email_addresses a ON a.address=n.address WHERE n.name LIKE ? ESCAPE '\\'";
                    let address = match field.as_str() {
                        "from" => format!("m.sender_address_pk IN ({matching})"),
                        "any" => format!("m.message_pk IN (WITH matching AS MATERIALIZED ({matching}) SELECT message_pk FROM messages WHERE sender_address_pk IN (SELECT address_pk FROM matching) UNION SELECT message_pk FROM recipients WHERE address_pk IN (SELECT address_pk FROM matching))"),
                        _ => format!("m.message_pk IN (SELECT message_pk FROM recipients WHERE address_pk IN ({matching}) AND role=?)"),
                    };
                    clauses.push(address);
                    values.extend([contains(value).into(), contains(value).into()]);
                    if !matches!(field.as_str(), "from" | "any") {
                        values.push(field.into());
                    }
                }
                _ => bail!("Unknown search selector: {field}:"),
            }
            terms.push(value.to_string());
        } else {
            fulltext.push(format!("\"{}\"", token.replace('"', "\"\"")));
            terms.push(token);
        }
    }
    if attachments {
        for term in fulltext {
            clauses.push("(EXISTS(SELECT 1 FROM search.message_fts WHERE rowid=mm.message_fts_rowid AND message_fts MATCH ?) OR EXISTS(SELECT 1 FROM search.attachment_fts WHERE rowid=mm.attachment_fts_rowid AND attachment_fts MATCH ?))".into());
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
            alternatives.push(format!("SELECT o.message_pk FROM source_files s JOIN observations o USING(source_file_pk) WHERE {}", conditions.join(" AND ")));
        }
        if !alternatives.is_empty() {
            clauses.push(format!(
                "m.message_pk IN ({})",
                alternatives.join(" UNION ")
            ));
        }
    }
    Ok((clauses, values, terms))
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
        assert_eq!(
            date_bounds("2020-01-01").unwrap().0,
            "2019-12-31T10:00:00+00:00"
        );
        let (_, values, _) = query_parts(r#"subject:"say \"hello\"""#).unwrap();
        assert_eq!(values, [SqlValue::Text("%say \"hello\"%".into())]);
        let (_, values, terms) = query_parts("subject:'annual report' ticket:123").unwrap();
        assert_eq!(terms, ["annual report", "ticket:123"]);
        assert_eq!(
            values,
            [
                SqlValue::Text("%annual report%".into()),
                SqlValue::Text("\"ticket:123\"".into())
            ]
        );
    }
}
