<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Archive Extensions

**Planned work.** These requirements/designs are not current executable features.
Existing partial capabilities are identified explicitly. See the
[implementation overview](../implementation.md) for the current system.

## Automatic catalog migrations

Schema files retain the Flyway naming convention
`V<version>__<description>.sql` (double underscore), such as
`V1__archive.sql`. This is a filename convention only, not a dependency on
Flyway, Java, or JDBC. Future catalog upgrades will use developer-written SQL
migrations run automatically by the application through SQLite; users must
not need a separate migration tool or manual SQL commands. The planned runner
must record applied versions and checksums, take a consistent SQLite backup,
hold the archive writer lease and pause affected windows, and commit each
migration with its history record atomically. It must reject unsupported newer
schemas and report failures without changing canonical MBOX bytes. Upgrade and
interruption tests are required before shipping migration support. Search keeps its
independent disposable rebuild path. Geography data has its own database version;
a snapshot explicitly copied into an archive follows archive migration history.
See the [geography snapshot policy](CONTACTS.md#database-migration-boundary).

## Sorting and integrated verification

* **Planned sorting:** At the end of an ingest run, touched normal MBOX files are
  sorted by resolved timestamp and then message SHA-256 for deterministic ties.
* **Planned sorting:** Sorting writes a same-directory temporary replacement and
  preserves the prior file as a backup.
* **Planned sorting:** The replacement and backup are parsed end-to-end. Their
  unordered sets of `(Message-ID, SHA-256)` must match exactly before the backup
  is deleted.
* A future integrated `verify` command is strictly read-only: it reparses every canonical MBOX,
  checks integrity files, offsets, and database rows, and reports duplicate-policy
  violations.

## Standalone printed-email PDFs

A standalone PDF containing scans of printed email is a source document, not
an attachment and not preserved RFC 5322 bytes. It is distinct from a PDF MIME
part inside a canonical message and from OCR or quoted-message text contained
in an actual message body.

* Extraction never rewrites the PDF. It records the complete PDF SHA-256,
  byte length, page count, exact source page for every derived message, the
  extraction and segmentation policies, and every page classified as
  message or non-message.
* Page text is an interchangeable input to segmentation. Native PDF text,
  local OCR, cloud OCR, and human transcription are versioned candidates;
  the message extractor must not depend on one OCR engine.
* Standard derived MBOX is the first validation and interchange format. Its
  records use synthetic identities and explicit `X-Mailarchiver-*` provenance,
  transcription status, PDF/page, policy, and handwriting declarations.
* A generated MBOX is written atomically and never over an existing file.
  It is not placed under canonical `data/mbox/`. Planned archive integration
  preserves source PDFs under `data/pdf/` and writes their reproducible
  `Archive-PDF` and `Sent-PDF` MBOX interpretations under `data/pdf-mbox/`.
* Handwritten annotations are recorded as a boolean page/message fact but
  their text is not indexed. Human review changes transcription status and
  corrected derived text without changing or obscuring the machine extract.
* Derived messages will appear immediately in ordinary search with a textual
  PDF badge and a color distinction. Every result opens its extracted text and
  exact source PDF page. Color must not be the only distinction.
* When normalized Date, Subject, To, and From are all present and identical to
  canonical email, the derived hit may be suppressed but remains stored and
  linked to its PDF pages. Edit-distance similarity creates only a suspected
  duplicate relation and does not suppress or merge the record.

## Redaction and research derivatives

* Redaction never edits canonical MBOX files. A redacted export identifies its
  source message hashes, policy and policy version, selected fields or byte
  ranges, reasons, operator, and creation time. Verification distinguishes an
  authorized transformation from corruption while preventing removed content
  from remaining in the public derivative or its ordinary indexes.
* Redaction policies can address headers, addresses, body passages, MIME parts,
  attachments, and derived entities. Decisions can be reviewed before export;
  access to the canonical-to-derivative audit mapping is controlled separately
  from access to the redacted corpus.
* The structured research database is derived and reproducible. It supports at
  least correspondents and aliases, dates, threads, message and attachment
  relationships, source provenance, and parse defects. Future entity and topic
  extraction records the extractor and version so reports can be reproduced or
  recomputed without changing canonical mail.
* Research reports state their corpus selection, exclusion/redaction policy,
  data and extractor versions, and known incompleteness. Derived facts never
  replace original headers or message bytes.

## Derivative implementation design

The exporter uses a versioned Pydantic policy over hash-verified messages and
emits an access-controlled canonical-to-derivative audit mapping. Research tables
record extractor, schema and policy versions. The preservation and disclosure
requirements are specified above; OCR details are in the [PDF/OCR plan](PDF_OCR.md).

## Sorting publication design

Publish sorted MBOX and integrity replacements together with the affected
`mbox_generations` and `locations` records. Retain the prior file until the
end-to-end comparison specified above succeeds. Quarantine is never promoted
to normal mail or FTS-extracted by sorting.
