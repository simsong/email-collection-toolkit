// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Restore folder browsing and saved searches behind the shared frontend.
// Folder identities come from original source observations, never archive paths.
// Counts exclude quarantined messages and deduplicate repeated source sightings.
// Search suggestions use catalog values with bound queries and finite deadlines.
// Saved filter sets are typed, versioned per-user preferences outside archives.
// UI callers receive the same data shapes as the existing Python interface.
use crate::selectors::{contains, date_bounds, Selection};
use anyhow::{ensure, Context, Result};
use rusqlite::Connection;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{collections::BTreeMap, io::Write, path::Path};

#[derive(Default)]
struct Folder {
    paths: BTreeMap<String, Folder>,
}
impl Folder {
    fn insert(&mut self, path: &str) {
        let mut node = self;
        for part in path.split('/').filter(|s| !s.is_empty()) {
            node = node.paths.entry(part.to_owned()).or_default();
        }
    }
}
pub(crate) fn tree(db: &Connection, volumes: bool) -> Result<Value> {
    let mut groups = BTreeMap::<String, (String, Folder)>::new();
    let mut statement = db.prepare("SELECT v.identity_json,v.metadata_json,s.hierarchy_path FROM source_files s JOIN source_volumes v USING(source_volume_pk) WHERE EXISTS(SELECT 1 FROM observations o JOIN messages m USING(message_pk) WHERE o.source_file_pk=s.source_file_pk AND m.category IN ('Archive','Sent'))")?;
    for row in statement.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
        ))
    })? {
        let (identity, metadata, path) = row?;
        let meta: Value = serde_json::from_str(&metadata).unwrap_or(Value::Null);
        let label = meta["volume_label"]
            .as_str()
            .or(meta["name"].as_str())
            .unwrap_or("Source volume")
            .to_string();
        groups
            .entry(if volumes { identity } else { String::new() })
            .or_insert_with(|| (label, Folder::default()))
            .1
            .insert(&path);
    }
    let mut result = Vec::new();
    for (identity, (label, folder)) in groups {
        let volume = volumes.then_some(identity);
        let children = nodes(db, &folder, "", &volume)?;
        if volumes {
            let selection = Selection {
                version: 1,
                path: String::new(),
                volume_identity: volume,
            };
            result.push(json!({"selection":selection.token()?,"logical_selection":Selection{volume_identity:None,..selection.clone()}.token()?,"label":label,"kind":"volume","count":count(db, &selection)?,"children":children}));
        } else {
            result.extend(children);
        }
    }
    Ok(json!(result))
}
fn count(db: &Connection, selection: &Selection) -> Result<i64> {
    let (clauses, values, _) =
        crate::selectors::plan("", false, Some(&json!([selection.token()?])))?;
    Ok(db.query_row(
        &format!(
            "SELECT count(*) FROM messages m WHERE {}",
            clauses.join(" AND ")
        ),
        rusqlite::params_from_iter(values),
        |r| r.get(0),
    )?)
}
fn nodes(
    db: &Connection,
    folder: &Folder,
    parent: &str,
    volume: &Option<String>,
) -> Result<Vec<Value>> {
    folder.paths.iter().map(|(label, folder)| {
        let path = if parent.is_empty() { label.clone() } else { format!("{parent}/{label}") };
        let selection = Selection { version: 1, path: path.clone(), volume_identity: volume.clone() };
        Ok(json!({"selection":selection.token()?,"logical_selection":Selection{volume_identity:None,..selection.clone()}.token()?,"label":label,"kind":if folder.paths.is_empty(){"mailbox"}else{"folder"},"count":count(db, &selection)?,"children":nodes(db,folder,&path,volume)?}))
    }).collect()
}

pub(crate) fn preferences_path() -> Result<std::path::PathBuf> {
    use std::path::PathBuf;
    #[cfg(target_os = "macos")]
    let root = PathBuf::from(std::env::var_os("HOME").context("HOME unavailable")?)
        .join("Library/Preferences");
    #[cfg(target_os = "windows")]
    let root = PathBuf::from(std::env::var_os("APPDATA").context("APPDATA unavailable")?);
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let root = std::env::var_os("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .unwrap_or(
            PathBuf::from(std::env::var_os("HOME").context("HOME unavailable")?).join(".config"),
        );
    ensure!(root.is_absolute(), "Preference directory must be absolute");
    Ok(root.join("mailarchiver/filter-sets.json"))
}

#[derive(Clone, Deserialize, Serialize)]
struct Filter {
    name: String,
    show_volumes: bool,
    selections: Vec<String>,
}
#[derive(Deserialize, Serialize)]
struct Filters {
    version: u8,
    filter_sets: Vec<Filter>,
}
pub(crate) fn filters(path: &Path, method: &str, args: &[Value]) -> Result<Value> {
    let _lock = if method == "saved_filter_sets" {
        None
    } else {
        Some(crate::documents::preferences_lock(path)?)
    };
    let mut store: Filters = match std::fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes).context("Invalid saved filters")?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Filters {
            version: 1,
            filter_sets: Vec::new(),
        },
        Err(e) => return Err(e.into()),
    };
    ensure!(store.version == 1, "Unsupported saved filters version");
    if method == "saved_filter_sets" {
        return Ok(serde_json::to_value(store)?);
    }
    let name = args
        .first()
        .and_then(Value::as_str)
        .context("Missing filter name")?
        .trim();
    ensure!(
        !name.is_empty() && !["none", "save..."].contains(&name.to_lowercase().as_str()),
        "Invalid filter name"
    );
    match method {
        "save_filter_set" => {
            let selections: Vec<String> =
                serde_json::from_value(args.get(2).cloned().context("Missing selections")?)?;
            for token in &selections {
                Selection::decode(token)?;
            }
            store.filter_sets.retain(|v| v.name != name);
            store.filter_sets.push(Filter {
                name: name.into(),
                show_volumes: args.get(1).and_then(Value::as_bool).unwrap_or(false),
                selections,
            });
        }
        "rename_filter_set" => {
            let next = args
                .get(1)
                .and_then(Value::as_str)
                .context("Missing new filter name")?
                .trim();
            ensure!(
                !next.is_empty() && !["none", "save..."].contains(&next.to_lowercase().as_str()),
                "Invalid filter name"
            );
            ensure!(
                !store
                    .filter_sets
                    .iter()
                    .any(|v| v.name == next && v.name != name),
                "Filter name already exists"
            );
            store
                .filter_sets
                .iter_mut()
                .find(|v| v.name == name)
                .context("Unknown filter name")?
                .name = next.into();
        }
        "delete_filter_set" => {
            ensure!(
                store.filter_sets.iter().any(|v| v.name == name),
                "Unknown filter name"
            );
            store.filter_sets.retain(|v| v.name != name);
        }
        _ => anyhow::bail!("Unknown filter operation"),
    }
    store.filter_sets.sort_by_key(|v| v.name.to_lowercase());
    let parent = path.parent().context("Missing preferences directory")?;
    std::fs::create_dir_all(parent)?;
    let mut output = tempfile::NamedTempFile::new_in(parent)?;
    serde_json::to_writer_pretty(&mut output, &store)?;
    output.flush()?;
    output.as_file().sync_all()?;
    output.persist(path)?;
    Ok(serde_json::to_value(store)?)
}

const TAGS: [&str; 9] = [
    "any", "from", "to", "cc", "bcc", "subject", "date", "before", "after",
];
fn completion_input(query: &str) -> (String, String, String) {
    let mut spans = Vec::new();
    let (mut start, mut quote, mut escape) = (None, None, false);
    for (i, ch) in query.char_indices() {
        if start.is_none() && !ch.is_whitespace() {
            start = Some(i);
        }
        if escape {
            escape = false;
            continue;
        }
        if ch == '\\' {
            escape = true;
            continue;
        }
        if quote == Some(ch) {
            quote = None;
        } else if quote.is_none() && matches!(ch, '\'' | '"') {
            quote = Some(ch);
        }
        if ch.is_whitespace() && quote.is_none() {
            if let Some(begin) = start.take() {
                spans.push((begin, i));
            }
        }
    }
    if let Some(begin) = start {
        spans.push((begin, query.len()));
    }
    let selectors = spans.iter().any(|(a, b)| {
        query[*a..*b]
            .split_once(':')
            .is_some_and(|(tag, _)| TAGS.contains(&tag.to_ascii_lowercase().as_str()))
    });
    let (prefix, tag, value) = if selectors && !spans.is_empty() {
        let (a, b) = spans.last().unwrap();
        let last = &query[*a..*b];
        let (tag, value) = last
            .split_once(':')
            .filter(|(tag, _)| TAGS.contains(&tag.to_ascii_lowercase().as_str()))
            .unwrap_or(("", last));
        (query[..*a].trim(), tag.to_ascii_lowercase(), value)
    } else {
        ("", String::new(), query.trim())
    };
    (
        prefix.into(),
        tag,
        value
            .trim_matches(['\'', '"'])
            .replace("\\\"", "\"")
            .replace("\\\\", "\\"),
    )
}
fn choice(tag: &str, count: i64) -> Value {
    json!({"tag":tag,"label":match tag {"any"=>"Any","from"=>"From","to"=>"To","cc"=>"Cc","bcc"=>"Bcc","subject"=>"Subject","date"=>"Date","before"=>"Before",_=>"After"},"message_count":count})
}
pub(crate) fn suggestions(db: &Connection, query: &str, limit: usize) -> Result<Value> {
    ensure!(
        (1..=50).contains(&limit),
        "Suggestion limit must be between 1 and 50"
    );
    let (prefix, tag, value) = completion_input(query);
    let mut items = Vec::new();
    if value.chars().count() < 3 {
        return Ok(json!({"query":query,"prefix":prefix,"items":items}));
    }
    let pattern = contains(&value);
    if tag.is_empty() || TAGS[..5].contains(&tag.as_str()) {
        let mut statement=db.prepare("WITH matching AS MATERIALIZED (SELECT address_pk FROM email_addresses WHERE address LIKE ?1 ESCAPE '\\' UNION SELECT a.address_pk FROM address_search_names n JOIN email_addresses a ON a.address=n.address WHERE n.name LIKE ?1 ESCAPE '\\'), hits AS MATERIALIZED (SELECT a.address_pk,'from' AS role,m.message_pk,m.date_utc FROM matching a CROSS JOIN messages m INDEXED BY messages_sender_address_pk ON m.sender_address_pk=a.address_pk WHERE m.category IN ('Archive','Sent') UNION ALL SELECT a.address_pk,r.role,m.message_pk,m.date_utc FROM matching a CROSS JOIN recipients r INDEXED BY recipients_address_pk USING(address_pk) JOIN messages m USING(message_pk) WHERE m.category IN ('Archive','Sent')), counts AS (SELECT address_pk,role,count(DISTINCT message_pk) AS n,max(date_utc) AS seen FROM hits GROUP BY address_pk,role UNION ALL SELECT address_pk,'any',count(DISTINCT message_pk),max(date_utc) FROM hits GROUP BY address_pk UNION ALL SELECT NULL,role,count(DISTINCT message_pk),max(date_utc) FROM hits GROUP BY role UNION ALL SELECT NULL,'any',count(DISTINCT message_pk),max(date_utc) FROM hits), ranked AS (SELECT address_pk FROM counts WHERE address_pk IS NOT NULL AND role=?2 ORDER BY n DESC,seen DESC,address_pk LIMIT ?3) SELECT coalesce(a.address,''),c.role,c.n,c.seen FROM counts c LEFT JOIN email_addresses a USING(address_pk) WHERE c.n>0 AND (c.address_pk IS NULL OR c.address_pk IN(SELECT address_pk FROM ranked))")?;
        let mut matches = BTreeMap::<String, Vec<Value>>::new();
        for row in statement.query_map(
            rusqlite::params![pattern, if tag.is_empty() { "any" } else { &tag }, limit],
            |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, i64>(2)?,
                ))
            },
        )? {
            let (address, role, count) = row?;
            matches
                .entry(address)
                .or_default()
                .push(choice(&role, count));
        }
        for (address, mut choices) in matches {
            choices.sort_by_key(|v| TAGS.iter().position(|tag| v["tag"] == *tag).unwrap_or(0));
            let roles: Vec<_> = choices.iter().filter(|v| v["tag"] != "any").collect();
            let selected = if !tag.is_empty() {
                tag.as_str()
            } else if roles.len() == 1 {
                roles[0]["tag"].as_str().unwrap()
            } else {
                "any"
            };
            if let Some(current) = choices.iter().find(|v| v["tag"] == selected) {
                let address = if address.is_empty() {
                    value.clone()
                } else {
                    address
                };
                items.push(json!({"tag":selected,"value":address,"label":address,"message_count":current["message_count"],"choices":choices}));
            }
        }
        items.sort_by_key(|v| {
            std::cmp::Reverse((
                v["value"] == value,
                v["message_count"].as_i64().unwrap_or(0),
            ))
        });
    }
    if tag.is_empty() || tag == "subject" {
        let count:i64=db.query_row("SELECT count(*) FROM messages WHERE category IN ('Archive','Sent') AND subject LIKE ? ESCAPE '\\'",[&pattern],|r|r.get(0))?;
        items.push(json!({"tag":"subject","value":value,"label":format!("Subject contains “{value}”"),"message_count":count,"choices":[choice("subject",count)]}));
        let mut statement=db.prepare("SELECT subject,count(*) FROM messages WHERE category IN ('Archive','Sent') AND subject LIKE ? ESCAPE '\\' GROUP BY subject ORDER BY count(*) DESC,lower(subject) LIMIT ?")?;
        for row in statement.query_map(rusqlite::params![pattern, limit], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
        })? {
            let (subject, count) = row?;
            items.push(json!({"tag":"subject","value":subject,"label":subject,"message_count":count,"choices":[choice("subject",count)]}));
        }
    }
    if (tag.is_empty() || TAGS[6..].contains(&tag.as_str())) && date_bounds(&value).is_ok() {
        let mut choices = Vec::new();
        for tag in &TAGS[6..] {
            let (clauses, values, _) =
                crate::selectors::query_parts(&format!("{tag}:\"{value}\""))?;
            let count: i64 = db.query_row(
                &format!(
                    "SELECT count(*) FROM messages m WHERE {}",
                    clauses.join(" AND ")
                ),
                rusqlite::params_from_iter(values),
                |r| r.get(0),
            )?;
            choices.push(choice(tag, count));
        }
        for option in &choices {
            if tag.is_empty() || option["tag"] == tag {
                items.push(json!({"tag":option["tag"],"value":value,"label":value,"message_count":option["message_count"],"choices":choices}));
            }
        }
    }
    Ok(json!({"query":query,"prefix":prefix,"items":items}))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn completions_preserve_selectors_and_incomplete_quotes() {
        assert_eq!(
            completion_input("from:alice subject:\"annual rep"),
            ("from:alice".into(), "subject".into(), "annual rep".into())
        );
        assert_eq!(
            completion_input("January 5, 2020"),
            ("".into(), "".into(), "January 5, 2020".into())
        );
        assert_eq!(
            completion_input("annual report"),
            ("".into(), "".into(), "annual report".into())
        );
    }
    #[test]
    fn saved_filters_round_trip_and_reject_malformed_tokens() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("filters.json");
        let token = Selection {
            version: 1,
            path: "mail/inbox".into(),
            volume_identity: None,
        }
        .token()
        .unwrap();
        filters(
            &path,
            "save_filter_set",
            &[json!("Work"), json!(true), json!([token])],
        )
        .unwrap();
        filters(
            &path,
            "rename_filter_set",
            &[json!("Work"), json!("Archive")],
        )
        .unwrap();
        let stored = filters(&path, "saved_filter_sets", &[]).unwrap();
        assert_eq!(stored["filter_sets"][0]["name"], "Archive");
        let before = std::fs::read(&path).unwrap();
        assert!(filters(
            &path,
            "save_filter_set",
            &[json!("Broken"), json!(false), json!(["bad"])]
        )
        .is_err());
        assert_eq!(std::fs::read(&path).unwrap(), before);
        filters(&path, "delete_filter_set", &[json!("Archive")]).unwrap();
        assert_eq!(
            filters(&path, "saved_filter_sets", &[]).unwrap()["filter_sets"],
            json!([])
        );
    }
}
