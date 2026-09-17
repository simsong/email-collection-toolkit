-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
-- Fresh CLI framework schema. Not a migration of a production V1 archive.
PRAGMA foreign_keys = ON;
CREATE TABLE schema_info(version INTEGER NOT NULL CHECK(version=2));
INSERT INTO schema_info VALUES(2);
CREATE TABLE messages (
 message_id TEXT PRIMARY KEY, normalized_message_id TEXT NOT NULL,
 sha256 TEXT NOT NULL, content_path TEXT NOT NULL,
 UNIQUE(normalized_message_id, sha256)
);
CREATE TABLE occurrences (
 occurrence_id INTEGER PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages,
 source TEXT NOT NULL, parent_message_id TEXT REFERENCES messages, part_path TEXT NOT NULL DEFAULT '[]',
 UNIQUE(message_id, source, part_path)
);
CREATE TABLE jobs (
 job_id INTEGER PRIMARY KEY, identity TEXT NOT NULL UNIQUE,
 parent_job_id INTEGER REFERENCES jobs,
 item_json TEXT NOT NULL, registry_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','aborted')),
 detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE invocations (
 invocation_id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs,
 kind TEXT NOT NULL, version TEXT NOT NULL, rank INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
 elapsed REAL NOT NULL DEFAULT 0, result_json TEXT, error TEXT,
 started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX invocations_job_kind ON invocations(job_id,kind,status);
CREATE TABLE persons(person_id INTEGER PRIMARY KEY, canonical_name TEXT NOT NULL, manual INTEGER NOT NULL DEFAULT 0 CHECK(manual IN(0,1)));
CREATE TABLE addresses(address_id INTEGER PRIMARY KEY, address TEXT NOT NULL UNIQUE, mailbox TEXT NOT NULL, domain TEXT NOT NULL);
CREATE TABLE person_addresses(
 address_id INTEGER PRIMARY KEY REFERENCES addresses, person_id INTEGER NOT NULL REFERENCES persons,
 manual INTEGER NOT NULL DEFAULT 0 CHECK(manual IN(0,1))
);
CREATE TABLE person_aliases(person_id INTEGER NOT NULL REFERENCES persons, name TEXT NOT NULL, PRIMARY KEY(person_id,name));
CREATE TABLE organizations(organization_id INTEGER PRIMARY KEY, name TEXT NOT NULL, manual INTEGER NOT NULL DEFAULT 0 CHECK(manual IN(0,1)));
CREATE TABLE organization_domains(domain TEXT PRIMARY KEY, organization_id INTEGER REFERENCES organizations, provider INTEGER NOT NULL DEFAULT 0 CHECK(provider IN(0,1)));
CREATE TABLE affiliations(
 affiliation_id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES persons,
 organization_id INTEGER NOT NULL REFERENCES organizations, start_date TEXT, end_date TEXT,
 manual INTEGER NOT NULL DEFAULT 0 CHECK(manual IN(0,1)), CHECK(start_date IS NULL OR end_date IS NULL OR start_date<=end_date)
);
CREATE TABLE evidence(
 evidence_id INTEGER PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages,
 part_path TEXT NOT NULL, plugin TEXT NOT NULL, version TEXT NOT NULL, kind TEXT NOT NULL,
 address_id INTEGER REFERENCES addresses, person_id INTEGER REFERENCES persons,
 organization_id INTEGER REFERENCES organizations, value TEXT NOT NULL,
 UNIQUE(message_id,part_path,plugin,version,kind,value)
);
CREATE TABLE tags(tagid INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, background_color TEXT, text_color TEXT, crosshatching TEXT, font TEXT);
INSERT INTO tags(name,background_color) VALUES('attachment','#f2f2f2');
CREATE TABLE message_tags(message_id TEXT NOT NULL REFERENCES messages, tagid INTEGER NOT NULL REFERENCES tags, PRIMARY KEY(message_id,tagid));
CREATE TABLE manual_decisions(decision_id INTEGER PRIMARY KEY, subject TEXT NOT NULL, operation TEXT NOT NULL, value TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE message_state(
 message_id TEXT PRIMARY KEY REFERENCES messages, root_item_json TEXT NOT NULL,
 parsed_json TEXT, headers_json TEXT, mime_json TEXT, scan_status TEXT,
 category TEXT, catalog_message_pk INTEGER, excluded INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE content_parts(
 message_id TEXT NOT NULL REFERENCES messages, part_path TEXT NOT NULL, producer TEXT NOT NULL,
 content_type TEXT NOT NULL, scope TEXT NOT NULL, synthetic INTEGER NOT NULL, text TEXT,
 PRIMARY KEY(message_id,part_path,producer)
);
CREATE TABLE message_addresses(
 message_id TEXT NOT NULL REFERENCES messages, address_id INTEGER NOT NULL REFERENCES addresses,
 seen_at TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY(message_id,address_id,kind)
);
CREATE TABLE processing_settings(name TEXT PRIMARY KEY, value TEXT NOT NULL);
