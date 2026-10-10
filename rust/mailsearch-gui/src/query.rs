// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Own complete search SQL for the native reader and its comparison executable.
// The shared selector compiler supplies normalized, bound filtering predicates.
// Filtering indexes select matches before ordering and display aggregation.
// Ordered IDs, header pages and bounded counts consume the same compiled plan.
// Keyset cursors advance across matching rows, never unrelated catalog windows.
// Tests explain and execute these production statements with unchanged values.
use crate::selectors::{self, Filter, Plan};
use anyhow::{bail, Context, Result};
use rusqlite::types::Value as SqlValue;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::borrow::Cow;

// Production processing keys are canonical SHA-256 hashes; catalog IDs are derived.
pub(crate) const ATTACHED_SEARCH_MESSAGES: &str = "CREATE TEMP VIEW attached_search_messages AS SELECT m.message_pk FROM main.messages m CROSS JOIN identities.message_state s ON s.message_id=m.sha256 AND s.catalog_message_pk=m.message_pk CROSS JOIN identities.tags t ON t.name='attachment' CROSS JOIN identities.message_tags mt ON mt.message_id=s.message_id AND mt.tagid=t.tagid";

pub(crate) struct Statement<'a> {
    pub sql: String,
    pub values: Vec<Cow<'a, SqlValue>>,
}
pub(crate) struct Query {
    plan: Plan,
    order: Order,
}
struct Order {
    key: &'static str,
    direction: &'static str,
    comparison: &'static str,
}
#[derive(Deserialize)]
struct Cursor {
    key: String,
    id: i64,
}
#[derive(Serialize)]
pub(crate) struct Header {
    pub message_pk: i64,
    pub sender: String,
    pub subject: String,
    pub date_utc: String,
    pub attachment_count: i64,
    pub recipients: String,
    pub attached_message: bool,
    #[serde(skip)]
    pub key: String,
}
#[cfg(test)]
mod ownership_tests {
    use super::*;
    #[test]
    fn header_json_moves_owned_strings_and_nested_values() {
        // requirements.md: replies retain data without recursively copying owned payloads.
        let subject = "large subject".repeat(1000);
        let pointer = subject.as_ptr();
        let header = Header {
            message_pk: 1,
            sender: "sender".into(),
            subject,
            date_utc: String::new(),
            attachment_count: 0,
            recipients: String::new(),
            attached_message: false,
            key: String::new(),
        };
        let value = owned_json!({"results":vec![Value::from(header)]});
        assert_eq!(
            value["results"][0]["subject"].as_str().unwrap().as_ptr(),
            pointer
        );
        let query = Query::parse("subject:hello", "date", "descending", false, None).unwrap();
        let statement = query.ids(None, Some(2)).unwrap();
        assert!(statement
            .values
            .iter()
            .any(|value| matches!(value, Cow::Borrowed(_))));
        assert!(std::ptr::eq(
            statement.values[0].as_ref(),
            &query.plan.values[0]
        ));
    }
}
impl Header {
    pub fn read(row: &rusqlite::Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            message_pk: row.get(0)?,
            sender: row.get(1)?,
            subject: row.get(2)?,
            date_utc: row.get(3)?,
            attachment_count: row.get(4)?,
            recipients: row.get(5)?,
            attached_message: false,
            key: row.get(6)?,
        })
    }
}
impl From<Header> for Value {
    fn from(header: Header) -> Self {
        owned_json!({"message_pk":header.message_pk,"sender":header.sender,"subject":header.subject,"date_utc":header.date_utc,"attachment_count":header.attachment_count,"recipients":header.recipients,"attached_message":header.attached_message})
    }
}
impl Query {
    pub fn parse(
        query: &str,
        sort: &str,
        direction: &str,
        attachments: bool,
        selections: Option<&Value>,
    ) -> Result<Self> {
        anyhow::ensure!(query.len() <= 4096, "Search is limited to 4096 bytes");
        let key = match sort {
            "date" => "m.date_utc",
            "subject" => "lower(m.subject)",
            "sender" => "lower(a.address)",
            _ => bail!("Unknown sort field"),
        };
        let (direction, comparison) = match direction {
            "ascending" => ("ASC", ">"),
            "descending" => ("DESC", "<"),
            _ => bail!("Unknown sort direction"),
        };
        Ok(Self {
            plan: selectors::plan(query, attachments, selections)?,
            order: Order {
                key,
                direction,
                comparison,
            },
        })
    }
    pub fn into_values(self) -> Vec<SqlValue> {
        self.plan.values
    }
    pub fn terms(&self) -> &[String] {
        &self.plan.terms
    }
    pub fn subject_completion(value: &str) -> Result<Self> {
        // Completion counts the literal fragment shown to the user, including spaces.
        let mut query = Self::parse("", "date", "descending", false, None)?;
        let (clause, value) = selectors::subject_predicate(&selectors::folded(value));
        query.plan.clauses.push(clause);
        query.plan.values.push(value);
        query.plan.filter = Filter::Membership;
        Ok(query)
    }
    fn source(&self) -> String {
        let source = match self.plan.filter {
            Filter::Hash => "messages m INDEXED BY messages_sha256",
            Filter::Membership => "messages m NOT INDEXED",
            Filter::Sender => "messages m INDEXED BY messages_sender_address_pk",
            Filter::Date => "messages m INDEXED BY messages_date_message",
            Filter::All if self.order.key == "m.date_utc" => "messages m INDEXED BY messages_date_message",
            Filter::All if self.order.key == "lower(m.subject)" => "messages m INDEXED BY messages_subject_message",
            Filter::All => return "email_addresses a INDEXED BY email_addresses_lower_address CROSS JOIN messages m INDEXED BY messages_sender_address_pk ON m.sender_address_pk=a.address_pk".into(),
        };
        format!("{source} JOIN email_addresses a ON a.address_pk=m.sender_address_pk")
    }
    fn selection(&self, cursor: Option<&Value>) -> Result<(String, Vec<Cow<'_, SqlValue>>)> {
        let mut selection = self.plan.clauses.join(" AND ");
        let mut values: Vec<_> = self.plan.values.iter().map(Cow::Borrowed).collect();
        if let Some(cursor) = cursor.filter(|v| !v.is_null()) {
            let cursor: Cursor = Cursor::deserialize(cursor).context("Invalid search cursor")?;
            selection.push_str(&format!(
                " AND ({},m.message_pk) {} (?,?)",
                self.order.key, self.order.comparison
            ));
            values.extend([Cow::Owned(cursor.key.into()), Cow::Owned(cursor.id.into())]);
        }
        Ok((selection, values))
    }
    fn limit(
        values: &mut Vec<Cow<'_, SqlValue>>,
        limit: Option<usize>,
        offset: usize,
    ) -> Result<String> {
        if let Some(limit) = limit {
            values.extend([
                Cow::Owned(i64::try_from(limit)?.into()),
                Cow::Owned(i64::try_from(offset)?.into()),
            ]);
            Ok(" LIMIT ? OFFSET ?".into())
        } else if offset > 0 {
            values.push(Cow::Owned(i64::try_from(offset)?.into()));
            Ok(" LIMIT -1 OFFSET ?".into())
        } else {
            Ok(String::new())
        }
    }
    pub fn ids(&self, cursor: Option<&Value>, limit: Option<usize>) -> Result<Statement<'_>> {
        self.ordered_ids(cursor, limit, false)
    }
    pub fn id_batch(&self, cursor: Option<&Value>) -> Result<Statement<'_>> {
        self.ordered_ids(cursor, Some(513), true)
    }
    fn ordered_ids(
        &self,
        cursor: Option<&Value>,
        limit: Option<usize>,
        key: bool,
    ) -> Result<Statement<'_>> {
        let (selection, mut values) = self.selection(cursor)?;
        let limit = Self::limit(&mut values, limit, 0)?;
        let projection = if key {
            format!(",{}", self.order.key)
        } else {
            String::new()
        };
        Ok(Statement { sql: format!("SELECT m.message_pk{projection} FROM {} WHERE {selection} ORDER BY {} {},m.message_pk {}{limit}",
            self.source(), self.order.key, self.order.direction, self.order.direction), values })
    }
    pub fn headers(
        &self,
        limit: Option<usize>,
        offset: usize,
        cursor: Option<&Value>,
    ) -> Result<Statement<'_>> {
        let (selection, mut values) = self.selection(cursor)?;
        let limit = Self::limit(&mut values, limit, offset)?;
        let sql = format!("WITH candidates AS MATERIALIZED (SELECT m.message_pk,m.sha256,a.address AS sender,m.subject,m.date_utc,{} AS order_key FROM {} WHERE {selection} ORDER BY {} {},m.message_pk {}{limit}) SELECT c.message_pk,c.sender,c.subject,c.date_utc,coalesce(mm.attachment_count,0),coalesce(group_concat(DISTINCT e.address),''),c.order_key FROM candidates c LEFT JOIN search.message_metadata mm USING(sha256) LEFT JOIN recipients r USING(message_pk) LEFT JOIN email_addresses e ON e.address_pk=r.address_pk GROUP BY c.message_pk ORDER BY c.order_key {},c.message_pk {}",
            self.order.key,self.source(),self.order.key,self.order.direction,self.order.direction,self.order.direction,self.order.direction);
        Ok(Statement { sql, values })
    }
    pub fn count(&self, maximum: Option<usize>) -> Result<Statement<'_>> {
        let (selection, mut values) = self.selection(None)?;
        let limit = Self::limit(&mut values, maximum, 0)?;
        Ok(Statement {
            sql: format!(
                "SELECT count(*) FROM (SELECT 1 FROM {} WHERE {selection}{limit})",
                self.source()
            ),
            values,
        })
    }
    pub fn subjects(&self, limit: usize) -> Result<Statement<'_>> {
        let (selection, mut values) = self.selection(None)?;
        values.push(Cow::Owned(i64::try_from(limit)?.into()));
        Ok(Statement { sql: format!("SELECT m.subject,count(*) FROM {} WHERE {selection} GROUP BY m.subject ORDER BY count(*) DESC,lower(m.subject) LIMIT ?", self.source()), values })
    }
    pub fn headers_for_ids(ids: &[i64]) -> Result<Statement<'static>> {
        let mut query = Self::parse("", "date", "descending", false, None)?;
        query.plan.clauses.push(format!(
            "m.message_pk IN ({})",
            vec!["?"; ids.len()].join(",")
        ));
        query
            .plan
            .values
            .extend(ids.iter().map(|id| SqlValue::Integer(*id)));
        query.plan.filter = Filter::Membership;
        let sql = query.headers(None, 0, None)?.sql;
        Ok(Statement {
            sql,
            values: query.plan.values.into_iter().map(Cow::Owned).collect(),
        })
    }
    pub fn attached_ids(ids: &[i64]) -> Statement<'static> {
        Statement {
            sql: format!(
                "SELECT DISTINCT message_pk FROM attached_search_messages WHERE message_pk IN ({})",
                vec!["?"; ids.len()].join(",")
            ),
            values: ids
                .iter()
                .map(|id| Cow::Owned(SqlValue::Integer(*id)))
                .collect(),
        }
    }
}

#[cfg(test)]
#[path = "query_tests.rs"]
mod tests;
