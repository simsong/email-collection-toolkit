# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Production CLI host for the durable ingest, message and content processor trees."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel
from tldextract import TLDExtract

from ..catalog import address_pk
from ..message import ParsedMessage, parse_message
from ..plugin_api import MailObject
from ..plugin_configuration import read_plugin_configuration
from ..plugin_loader import builtin_plugin_directory
from ..search import PreparedSearchMessage, SEARCH_CATEGORIES, body_preview, write_prepared_search_message
from .api import (
    ApplicationContext, ArchiveContext, ContentReference, Pipeline, PluginSpec, ProcessingObject,
    ProcessingResult, RAW_MESSAGE, RunReport,
)
from .contracts import AddressEvidence, ContentMetadata, HeaderMetadata, MailboxReference, MimeInventory, ProcessingPolicy, SourceMetadata
from .registry import fingerprint, load_processors
from .runtime import run
from .store import DATABASE, connect, enqueue, report, snapshot

DOMAIN = TLDExtract(cache_dir=None, suffix_list_urls=())
Publisher = Callable[[MailObject, ParsedMessage, MailboxReference, int], int]


class IngestedMessage(BaseModel):
    parsed: ParsedMessage
    message_pk: int | None
    duplicate: bool
    excluded: bool


class ProductionPipeline:
    def __init__(self, archive: Path, catalog: sqlite3.Connection, search: sqlite3.Connection,
                 policy: ProcessingPolicy, publish: Publisher, *, plugin_dirs: tuple[Path, ...] = (),
                 installation_config: Path | None = None, cancelled: Callable[[], None] | None = None) -> None:
        self.archive = archive.resolve()
        self.catalog = catalog
        self.search = search
        self.policy = policy
        self.publisher = publish
        self.installation_config = installation_config
        self.cancelled = cancelled
        self.plugins = load_processors((builtin_plugin_directory(), *plugin_dirs))
        self.registry_hash = fingerprint(self.plugins)
        self.database = connect(self.archive, create=not (self.archive / DATABASE).exists(), production=True)
        self.configuration_hash = self._configuration_hash()
        previous = self.database.execute("SELECT value FROM processing_settings WHERE name='configuration'").fetchone()
        if previous and previous[0] != self.configuration_hash:
            self.reprocess()

    def _configuration_hash(self) -> str:
        digest = hashlib.sha256(self.registry_hash.encode())
        digest.update(self.policy.model_dump_json(exclude={"scanner_configuration", "scanner_executable"}).encode())
        for path in sorted(Path(__file__).parent.glob("*.py")):
            digest.update(path.read_bytes())
        for plugin in self.plugins:
            settings = read_plugin_configuration(self.archive, plugin.manifest.kind, self.installation_config)
            digest.update(json.dumps(settings.get_my_config(), sort_keys=True).encode())
        return digest.hexdigest()

    def close(self) -> None:
        # Plugin-owned settings written in this run are part of its accepted state.
        with self.database:
            self.database.execute("INSERT OR REPLACE INTO processing_settings VALUES('configuration',?)", (self._configuration_hash(),))
            self.database.execute("INSERT OR REPLACE INTO processing_settings VALUES('policy',?)",
                (self.policy.model_dump_json(exclude={"scanner_configuration"}),))
        self.database.close()

    def prepare(self, item: ProcessingObject) -> ProcessingObject:
        if self.cancelled is not None:
            self.cancelled()
        row = self.database.execute("SELECT scan_status FROM message_state WHERE message_id=?", (item.message_id,)).fetchone()
        return item.model_copy(update={"application": ApplicationContext(policy=self.policy),
                                      "scan_provenance": row[0] if row and row[0] else item.scan_provenance})

    def _admit(self, reference: ContentReference, metadata: SourceMetadata, *, pipeline: Pipeline = "ingest",
               parent: str | None = None, part_path: tuple[int, ...] = (), scan: str | None = None, depth: int = 0) -> ProcessingObject:
        item = ProcessingObject(archive=ArchiveContext(path=self.archive), application=ApplicationContext(policy=self.policy),
            message_id=reference.sha256, message_ref=reference, content_ref=reference, content_type=RAW_MESSAGE,
            pipeline=pipeline, source_metadata=metadata, parent_message_id=parent, scan_provenance=scan,
            content_metadata=ContentMetadata(depth=depth))
        self.database.execute("INSERT OR IGNORE INTO messages VALUES(?,?,?,?)",
                              (item.message_id, item.message_id, reference.sha256, str(reference.path)))
        self.database.execute("INSERT OR IGNORE INTO message_state(message_id,root_item_json,scan_status) VALUES(?,?,?)",
                              (item.message_id, item.model_dump_json(), scan))
        self.database.execute("INSERT OR IGNORE INTO occurrences(message_id,source,parent_message_id,part_path) VALUES(?,?,?,?)",
                              (item.message_id, f"{metadata.source_file_pk}:{metadata.cursor}", parent, json.dumps(part_path)))
        return item

    def ingest(self, source: MailObject, metadata: SourceMetadata) -> IngestedMessage:
        with tempfile.NamedTemporaryFile(dir=self.archive, prefix=".processing-input-") as temporary:
            temporary.write(source.raw)
            temporary.flush()
            reference = snapshot(self.archive, Path(temporary.name))
        existing = self.catalog.execute("SELECT message_pk FROM messages WHERE sha256=?", (reference.sha256,)).fetchone()
        with self.database:
            item = self._admit(reference, metadata)
            state = self.database.execute("SELECT parsed_json FROM message_state WHERE message_id=?", (item.message_id,)).fetchone()
            if state is None or state[0] is None:
                enqueue(self.database, item, self.registry_hash)
        self.resume(("ingest", "message"))
        row = self.database.execute("SELECT parsed_json,catalog_message_pk,excluded FROM message_state WHERE message_id=?", (item.message_id,)).fetchone()
        if row is None or row[0] is None:
            raise RuntimeError("ingest processor tree ended without a filing decision")
        return IngestedMessage(parsed=ParsedMessage.model_validate_json(row[0]), message_pk=row[1], duplicate=existing is not None, excluded=bool(row[2]))

    def resume(self, pipelines: tuple[Pipeline, ...], *, max_jobs: int | None = None) -> RunReport:
        failures: list[Exception] = []
        result = run(self.database, self.plugins, retry=True, pipelines=pipelines, max_jobs=max_jobs,
                     installation_config=self.installation_config, services=self, cancelled=self.cancelled, failures=failures)
        placeholders = ",".join("?" for _ in pipelines)
        failed = self.database.execute(f"SELECT detail FROM jobs WHERE status='failed' AND json_extract(item_json,'$.pipeline') IN ({placeholders}) LIMIT 1", pipelines).fetchone()
        if failed:
            error = self.database.execute("SELECT error,kind FROM invocations WHERE status='failed' ORDER BY invocation_id DESC LIMIT 1").fetchone()
            prefix = "failed to parse message" if error and error[1] in ("file-message", "headers") else "processor failed"
            raise RuntimeError(f"{prefix}: {error[0] if error else failed[0]}") from (failures[-1] if failures else None)
        return result

    def reprocess(self) -> None:
        generation = str(uuid4())
        with self.database:
            self.database.execute("UPDATE jobs SET status='aborted',detail='superseded processing configuration' WHERE status IN ('pending','running','failed')")
            self.database.execute("DELETE FROM content_parts")
            self.database.execute("DELETE FROM evidence")
            self.database.execute("DELETE FROM message_addresses")
            self.database.execute("DELETE FROM affiliations WHERE manual=0")
            for payload, message_pk, scan in self.database.execute("SELECT root_item_json,catalog_message_pk,scan_status FROM message_state WHERE excluded=0 AND coalesce(category,'')<>'INFECTED'").fetchall():
                original = ProcessingObject.model_validate_json(payload)
                pipeline: Pipeline = "ingest" if message_pk is None else "message"
                item = original.model_copy(update={"generation": generation, "pipeline": pipeline, "scan_provenance": scan})
                enqueue(self.database, item, self.registry_hash)

    def _file(self, item: ProcessingObject, parsed: ParsedMessage, mailbox: MailboxReference | None) -> None:
        self.database.execute("UPDATE messages SET normalized_message_id=? WHERE message_id=?", (parsed.message_id, item.message_id))
        if mailbox is None:
            self.database.execute("UPDATE message_state SET parsed_json=?,excluded=1 WHERE message_id=?", (parsed.model_dump_json(), item.message_id))
            return
        expected = item.archive.mailbox("INFECTED", None) if mailbox.category == "INFECTED" else item.archive.mailbox(
            datetime.fromisoformat(parsed.date_utc).year, "Sender" if mailbox.category == "Sent" else "Archive")
        if mailbox != expected or item.source_metadata is None:
            raise ValueError("invalid mailbox service request or missing source provenance")
        existing = self.catalog.execute("SELECT message_pk FROM messages WHERE message_id_normalized=? AND sha256=?", (parsed.message_id, parsed.sha256)).fetchone()
        if existing:
            message_pk = int(existing[0])
        else:
            source = item.source_metadata.mail_object(item.message_ref.path.read_bytes())
            message_pk = self.publisher(source, parsed, mailbox, item.source_metadata.source_file_pk)
        self.database.execute("UPDATE message_state SET parsed_json=?,catalog_message_pk=?,category=?,excluded=0 WHERE message_id=?",
                              (parsed.model_dump_json(), message_pk, mailbox.category, item.message_id))

    def publish(self, database: sqlite3.Connection, job_id: int, item: ProcessingObject,
                plugin: PluginSpec, result: ProcessingResult) -> None:
        if result.scan is not None:
            database.execute("UPDATE message_state SET scan_status=? WHERE message_id=?", (result.scan.status, item.message_id))
        if result.filing is not None:
            self._file(item, result.filing.parsed, result.filing.mailbox)
        row = database.execute("SELECT catalog_message_pk,parsed_json FROM message_state WHERE message_id=?", (item.message_id,)).fetchone()
        message_pk = row[0] if row else None
        if result.headers is not None and message_pk is not None:
            parsed = result.headers.parsed
            with self.catalog:
                self.catalog.execute("UPDATE messages SET sender_address_pk=?,subject=?,date_utc=?,date_source=? WHERE message_pk=?",
                    (address_pk(self.catalog, parsed.sender), parsed.subject, parsed.date_utc, parsed.date_source, message_pk))
                self.catalog.execute("DELETE FROM recipients WHERE message_pk=?", (message_pk,))
                self.catalog.executemany("INSERT INTO recipients(message_pk,address_pk,role) VALUES(?,?,?)",
                    ((message_pk, address_pk(self.catalog, recipient.address), recipient.role.value) for recipient in parsed.recipients))
            database.execute("UPDATE message_state SET parsed_json=?,headers_json=? WHERE message_id=?",
                              (parsed.model_dump_json(), result.headers.model_dump_json(), item.message_id))
        if result.mime is not None:
            database.execute("UPDATE message_state SET mime_json=? WHERE message_id=?", (result.mime.model_dump_json(), item.message_id))
        if result.text is not None:
            database.execute("INSERT OR REPLACE INTO content_parts VALUES(?,?,?,?,?,?,?)",
                (item.message_id, json.dumps(item.part_path), plugin.manifest.kind, item.content_type, item.scope, int(item.synthetic), result.text.text))
        self._evidence(item, plugin, result.evidence)
        if message_pk is not None:
            with self.catalog:
                self.catalog.executemany("INSERT OR IGNORE INTO metadata_defects(message_pk,field,detail) VALUES(?,?,?)",
                    ((message_pk, plugin.manifest.kind, detail) for detail in result.diagnostics))
        for child in result.promotions:
            if item.source_metadata is None:
                raise ValueError("child message has no source provenance")
            metadata = item.source_metadata.model_copy(update={
                "cursor": item.source_metadata.cursor + "#mime=" + ".".join(map(str, child.part_path)),
                "envelope_hex": None, "normalization": None,
                "prior_date": datetime.fromisoformat(ParsedMessage.model_validate_json(row[1]).date_utc) if row and row[1] else item.source_metadata.prior_date})
            child_item = self._admit(child.content_ref, metadata, pipeline="message", parent=item.message_id,
                part_path=child.part_path, scan=item.scan_provenance, depth=item.content_metadata.depth + 1)
            parsed = parse_message(child.content_ref.path.read_bytes(), metadata.source_path, metadata.prior_date, metadata.source_date, self.policy.earliest_year)
            mailbox = child_item.archive.mailbox(datetime.fromisoformat(parsed.date_utc).year, "Sender" if self.policy.owners.matches(parsed.sender) else "Archive")
            self._file(child_item, parsed, mailbox)
            database.execute("INSERT OR IGNORE INTO message_tags SELECT ?,tagid FROM tags WHERE name='attachment'", (child_item.message_id,))
            enqueue(database, child_item, self.registry_hash, job_id)

    def _evidence(self, item: ProcessingObject, plugin: PluginSpec, evidence: tuple[AddressEvidence, ...]) -> None:
        row = self.database.execute("SELECT parsed_json FROM message_state WHERE message_id=?", (item.message_id,)).fetchone()
        if not row or not row[0]:
            return
        seen = ParsedMessage.model_validate_json(row[0]).date_utc
        part_path = json.dumps(item.part_path)
        self.database.execute("DELETE FROM evidence WHERE message_id=? AND part_path=? AND plugin=?", (item.message_id, part_path, plugin.manifest.kind))
        for entry in evidence:
            address = entry.address.strip().lower()
            mailbox, separator, domain = address.rpartition("@")
            if not separator or not mailbox or not domain:
                continue
            address_id = self.database.execute("INSERT INTO addresses(address,mailbox,domain) VALUES(?,?,?) ON CONFLICT(address) DO UPDATE SET address=excluded.address RETURNING address_id", (address, mailbox, domain)).fetchone()[0]
            person = self.database.execute("SELECT person_id FROM person_addresses WHERE address_id=?", (address_id,)).fetchone()
            if person:
                person_id = person[0]
                if entry.name:
                    self.database.execute("UPDATE persons SET canonical_name=? WHERE person_id=? AND manual=0 AND canonical_name=?", (entry.name, person_id, address))
            else:
                person_id = self.database.execute("INSERT INTO persons(canonical_name) VALUES(?) RETURNING person_id", (entry.name or address,)).fetchone()[0]
                self.database.execute("INSERT INTO person_addresses VALUES(?,?,0)", (address_id, person_id))
            if entry.name:
                self.database.execute("INSERT OR IGNORE INTO person_aliases VALUES(?,?)", (person_id, entry.name))
            parent_domain = DOMAIN(domain).top_domain_under_public_suffix or ".".join(domain.split(".")[-2:])
            organization = self.database.execute("SELECT organization_id FROM organization_domains WHERE domain=?", (parent_domain,)).fetchone()
            if organization:
                organization_id = organization[0]
            else:
                organization_id = self.database.execute("INSERT INTO organizations(name) VALUES(?) RETURNING organization_id", (parent_domain,)).fetchone()[0]
                self.database.execute("INSERT INTO organization_domains VALUES(?,?,0)", (parent_domain, organization_id))
            affiliation = self.database.execute("SELECT affiliation_id FROM affiliations WHERE person_id=? AND organization_id=? AND manual=0", (person_id, organization_id)).fetchone()
            if affiliation:
                self.database.execute("UPDATE affiliations SET start_date=min(start_date,?),end_date=max(end_date,?) WHERE affiliation_id=?", (seen, seen, affiliation[0]))
            else:
                self.database.execute("INSERT INTO affiliations(person_id,organization_id,start_date,end_date) VALUES(?,?,?,?)", (person_id, organization_id, seen, seen))
            self.database.execute("INSERT OR IGNORE INTO message_addresses VALUES(?,?,?,?)", (item.message_id, address_id, seen, entry.kind))
            self.database.execute("INSERT OR IGNORE INTO evidence(message_id,part_path,plugin,version,kind,address_id,person_id,organization_id,value) VALUES(?,?,?,?,?,?,?,?,?)",
                (item.message_id, part_path, plugin.manifest.kind, plugin.manifest.implementation_version, entry.kind, address_id, person_id, organization_id, entry.model_dump_json()))

    def complete(self, item: ProcessingObject) -> None:
        if item.pipeline != "content":
            return
        row = self.database.execute("SELECT headers_json,mime_json,category FROM message_state WHERE message_id=?", (item.message_id,)).fetchone()
        if not row or not row[0] or row[2] not in SEARCH_CATEGORIES:
            return
        headers = HeaderMetadata.model_validate_json(row[0])
        inventory = MimeInventory.model_validate_json(row[1]) if row[1] else MimeInventory()
        parts = self.database.execute("SELECT part_path,scope,text FROM content_parts WHERE message_id=?", (item.message_id,)).fetchall()
        parts.sort(key=lambda part: json.loads(part[0]))
        body = "\n".join(part[2] for part in parts if part[1] == "body")
        attachments = "\n".join(part[2] for part in parts if part[1] == "attachment") if self.policy.index_attachments else ""
        with self.search:
            write_prepared_search_message(self.search, PreparedSearchMessage(sha256=item.message_id,
                content=headers.search_headers + "\n" + body, attachment_content=attachments,
                attachments=list(inventory.attachments), suggestions=list(headers.suggestions),
                preview=body_preview(body), subject=headers.parsed.subject), date_utc=headers.parsed.date_utc)

    def statistics(self) -> RunReport:
        return report(self.database, tuple(plugin.manifest.kind for plugin in self.plugins))
