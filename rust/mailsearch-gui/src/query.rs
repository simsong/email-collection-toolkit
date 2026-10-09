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

// Production processing keys are canonical SHA-256 hashes; catalog IDs are derived.
pub(crate) const ATTACHED_SEARCH_MESSAGES: &str = "CREATE TEMP VIEW attached_search_messages AS SELECT m.message_pk FROM main.messages m CROSS JOIN identities.message_state s ON s.message_id=m.sha256 AND s.catalog_message_pk=m.message_pk CROSS JOIN identities.tags t ON t.name='attachment' CROSS JOIN identities.message_tags mt ON mt.message_id=s.message_id AND mt.tagid=t.tagid";

pub(crate) struct Statement {
    pub sql: String,
    pub values: Vec<SqlValue>,
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
    fn selection(&self, cursor: Option<&Value>) -> Result<(String, Vec<SqlValue>)> {
        let mut clauses = self.plan.clauses.clone();
        let mut values = self.plan.values.clone();
        if let Some(cursor) = cursor.filter(|v| !v.is_null()) {
            let cursor: Cursor =
                serde_json::from_value(cursor.clone()).context("Invalid search cursor")?;
            clauses.push(format!(
                "({},m.message_pk) {} (?,?)",
                self.order.key, self.order.comparison
            ));
            values.extend([cursor.key.into(), cursor.id.into()]);
        }
        Ok((clauses.join(" AND "), values))
    }
    fn limit(values: &mut Vec<SqlValue>, limit: Option<usize>, offset: usize) -> Result<String> {
        if let Some(limit) = limit {
            values.extend([i64::try_from(limit)?.into(), i64::try_from(offset)?.into()]);
            Ok(" LIMIT ? OFFSET ?".into())
        } else if offset > 0 {
            values.push(i64::try_from(offset)?.into());
            Ok(" LIMIT -1 OFFSET ?".into())
        } else {
            Ok(String::new())
        }
    }
    pub fn ids(&self, cursor: Option<&Value>, limit: Option<usize>) -> Result<Statement> {
        self.ordered_ids(cursor, limit, false)
    }
    pub fn id_batch(&self, cursor: Option<&Value>) -> Result<Statement> {
        self.ordered_ids(cursor, Some(513), true)
    }
    fn ordered_ids(
        &self,
        cursor: Option<&Value>,
        limit: Option<usize>,
        key: bool,
    ) -> Result<Statement> {
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
    ) -> Result<Statement> {
        let (selection, mut values) = self.selection(cursor)?;
        let limit = Self::limit(&mut values, limit, offset)?;
        let sql = format!("WITH candidates AS MATERIALIZED (SELECT m.message_pk,m.sha256,a.address AS sender,m.subject,m.date_utc,{} AS order_key FROM {} WHERE {selection} ORDER BY {} {},m.message_pk {}{limit}) SELECT c.message_pk,c.sender,c.subject,c.date_utc,coalesce(mm.attachment_count,0),coalesce(group_concat(DISTINCT e.address),''),c.order_key FROM candidates c LEFT JOIN search.message_metadata mm USING(sha256) LEFT JOIN recipients r USING(message_pk) LEFT JOIN email_addresses e ON e.address_pk=r.address_pk GROUP BY c.message_pk ORDER BY c.order_key {},c.message_pk {}",
            self.order.key,self.source(),self.order.key,self.order.direction,self.order.direction,self.order.direction,self.order.direction);
        Ok(Statement { sql, values })
    }
    pub fn count(&self, maximum: Option<usize>) -> Result<Statement> {
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
    pub fn subjects(&self, limit: usize) -> Result<Statement> {
        let (selection, mut values) = self.selection(None)?;
        values.push(i64::try_from(limit)?.into());
        Ok(Statement { sql: format!("SELECT m.subject,count(*) FROM {} WHERE {selection} GROUP BY m.subject ORDER BY count(*) DESC,lower(m.subject) LIMIT ?", self.source()), values })
    }
    pub fn headers_for_ids(ids: &[i64]) -> Result<Statement> {
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
        query.headers(None, 0, None)
    }
    pub fn attached_ids(ids: &[i64]) -> Statement {
        Statement {
            sql: format!(
                "SELECT DISTINCT message_pk FROM attached_search_messages WHERE message_pk IN ({})",
                vec!["?"; ids.len()].join(",")
            ),
            values: ids.iter().map(|id| SqlValue::Integer(*id)).collect(),
        }
    }
}

#[cfg(test)]
#[path = "query_tests.rs"]
mod tests;
