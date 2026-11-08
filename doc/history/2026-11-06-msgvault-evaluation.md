<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# 2026-11-06 msgvault evaluation

**Dated comparison.** This report describes Email Collection Toolkit (ECT)
baseline `ba0af6c5` (`1.0.0a54`) and msgvault commit
[`3b9912c3`](https://github.com/kenn-io/msgvault/commit/3b9912c3631b515266d1131d1611d5abbb404f9b),
reviewed on 2026-11-06. Msgvault is active alpha software; recheck its docs and
implementation before making a later adoption decision. This is a product and
architecture comparison, not a claim that either project's every feature has
passed all platform acceptance.

## Recommendation

**Do not abandon ECT, and do not try to reproduce msgvault feature-for-feature.**
Msgvault is a much broader personal communications manager: it combines email
with chat, meetings, calendars, contacts, people/relationship features, and
multiple user interfaces. ECT addresses a narrower but different problem:
building a byte-preserving, independently verifiable email preservation
collection from heterogeneous sources, with source observations and explicit
accounting. Msgvault's breadth does not replace that preservation contract.

Take selected operational and search ideas only where they fit ECT's existing
boundaries. In particular, study its backup/restore workflow, explicit
verification limits, structured discovery, and optional derived-content
indexing. Keep MBOX and BagIt/Mailbag—not a private database—the canonical
record. Do not copy upstream deletion, survivor-selection, or other behavior
that can remove or replace evidence.

## Feature comparison

| Area | ECT at the reviewed baseline | msgvault at the reviewed commit | What the difference means |
|---|---|---|---|
| **Product boundary** | Email preservation and research infrastructure; not a mail client or compliance appliance. Its goals put heterogeneous acquisition, source provenance, canonical content, and derivatives in separate roles. ([ECT README](../../README.md#goals), [archive requirements](../requirements/ARCHIVE.md#purpose)) | Local-first archive and manager for email, chat, meetings, calendars, and contacts, with people/profile features. | Msgvault is a stronger fit for someone seeking one personal communications and relationship workspace. ECT's narrower scope is a feature when custody and long-term interchange matter most. |
| **Canonical storage** | Canonical messages are byte-preserving MBOX in a BagIt 1.0 bag conforming to Mailbag 1.0. SHA-256 file manifests and per-message integrity declarations allow independent verification; the installed verifier uses the Python standard library and does not need SQLite. ([archive layout](../requirements/ARCHIVE.md#canonical-archive-layout), [integrity controls](../INTEGRITY_CONTROLS.md#scope)) | SQLite is the default archive store, with attachment files addressed by SHA-256. It exports normalized records and original raw MIME, but its documented primary archive is not a BagIt/Mailbag MBOX package. ([architecture](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/architecture/overview.md), [exporting](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/usage/exporting.md)) | This is ECT's clearest distinction. Its data remains usable and checkable without ECT's application or database; msgvault's database and export/backup path serve a different storage model. |
| **Local email sources** | Supports MBOX, EML, Maildir, Emacs RMAIL/Babyl, complete Apple Mail `.emlx`, and PST/OST paths, as well as read-only IMAP; Gmail Takeout MBOX is handled as MBOX. Each adapter has source-specific completeness, recovery, and provenance rules. ([acquisition requirements](../requirements/ACQUISITION.md#ingest-sources), [format inventory](../ON_DISK_MAIL_FORMATS.md)) | Documents MBOX, Maildir, MailMate-style EML directories, Apple Mail, and PST import. It also documents live Gmail, IMAP, and Microsoft mail. ([source guide](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/guides/sources.md)) | ECT explicitly documents Babyl and OST paths that are not listed in msgvault's source guide. Msgvault's breadth of provider and non-email sources is substantially greater. This is a documentation-level comparison, not proof that an unlisted format is impossible in msgvault. |
| **Live acquisition** | Its delivered live path is a standalone, one-shot, read-only IMAP downloader. It uses `EXAMINE` and `BODY.PEEK`, leaves source messages and flags unchanged, checkpoints progress, and retains wire-hash evidence for its documented CRLF-to-LF conversion. Direct Gmail API/OAuth and Microsoft 365 OAuth are not current features. ([IMAP requirements](../requirements/ACQUISITION.md#standalone-imap-download-cli)) | Offers provider sync including Gmail, IMAP and Microsoft Graph/IMAP, plus other communications providers; supports recurring/background work. Provider permissions and available history vary, and optional enrichment can use configured services. ([source guide](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/guides/sources.md), [README](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/README.md)) | Msgvault wins on connected-source breadth and ongoing sync. ECT should adopt additional provider paths only as explicitly read-only, resumable ECT adapters with source evidence—not as permission to alter mailboxes. |
| **Duplicate handling and provenance** | Uses content-aware message identity and retains separate source observations for repeats. A shared Message-ID is not enough to replace different bytes. ([deduplication and provenance](../requirements/ARCHIVE.md#deduplication-and-provenance)) | Detects duplicates using Message-ID and an optional normalized raw-MIME hash pass; its review workflow selects a survivor, hides duplicate rows by default, and has later hard-delete/garbage-collection operations. ([deduplication](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/usage/deduplication.md)) | ECT's preservation policy is stronger for retaining distinct source evidence and conflicting variants. Msgvault's duplicate review UI may inform presentation, but its survivor and deletion semantics must not be transplanted into canonical ingest. |
| **Integrity and verification** | Separates source-container fixity, canonical message hashes, MBOX fixity, semantic hashes, and rebuildable indexes. The standalone verifier checks bag structure and hashes without the operational database. ([integrity controls](../INTEGRITY_CONTROLS.md)) | Offers local backup verification and Gmail-oriented verification. Its guide spells out limits: a successful provider check does not establish message-ID equivalence, complete attachment acquisition, or full-text-index coverage. ([verification guide](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/guides/verification.md)) | ECT's verifier checks its preservation package; neither project's single verification command should be read as proof of every aspect of source completeness. Msgvault's explicit statement of non-goals is a useful documentation practice. |
| **Search, analysis, and presentation** | Local desktop search and message viewing use rebuildable SQLite/FTS indexes. ECT provides email-specific provenance and research metadata. PDF/Office attachment text extraction is not yet connected to ordinary ingest/search. ([search requirements](../requirements/SEARCH.md), [README](../plans/PDF_OCR.md)) | Browser UI, TUI, CLI, HTTP API, and MCP; keyword, semantic/hybrid search, analytics, people/profile exploration, and optional document/image indexing. Some optional features send selected data to configured endpoints. ([README](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/README.md), [architecture](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/architecture/overview.md)) | Msgvault is markedly ahead as an integrated search and day-to-day interaction product. ECT should keep analysis/indexes derivative and opt-in where processing leaves the machine. |
| **Backup and recovery** | Imports checkpoint and resume, and the archive itself has MBOX plus BagIt manifests. The README warns that MBOX alone does not restore durable manual identities, tags, configuration, and operational history; the whole archive directory must be backed up. ([archive care](../../README.md#archive-care)) | Has a distinct incremental, checksummed backup repository with create/list/verify/restore commands. Its docs also clearly disclose that repository encryption and retention/pruning are not implemented and that old snapshots may retain purged content. ([backup guide](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/usage/backup.md)) | Msgvault provides a more explicit, purpose-built backup workflow. This is a valuable ECT gap to address, but the workflow should back up the complete ECT archive and durable state without substituting database-page snapshots for canonical MBOX/BagIt. |
| **Safety and privacy** | Source mailboxes and input archives are read-only. IMAP passwords use the native OS keyring and verified TLS. Embedded libclamav scans imported mail; positive or failed scans are reported and retained rather than deleting or rewriting messages. ([source safety](../../AGENTS.md#scope-and-source-safety), [scanner requirements](../requirements/SCANNING.md#malware-handling)) | Documents filesystem-level protections and provider authorization, discloses anonymous usage telemetry, and says database/OAuth token contents are not encrypted at rest by the application. It has separate staged and explicit provider-deletion workflows. ([security policy](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/SECURITY.md), [deletion guide](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/usage/deletion.md)) | ECT's immutable-source and retained-message rules differ from msgvault's broader account-management role. They are product choices, not a checklist where more operations are automatically better. |
| **Platforms and maturity** | Current packaged desktop targets are macOS and Windows; ECT describes itself as in active development and says not to use it operationally before 1.0. ([README](../../README.md#desktop-application)) | Documents macOS, Linux, and Windows; describes itself as alpha and warns that APIs, storage format, and CLI flags may change. ([README](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/README.md)) | Both need maturity caveats. Msgvault's faster-changing breadth does not establish archival-format suitability or prove release reliability. |

## What ECT does that msgvault does not document as its goal

These are ECT's documented differentiators, not claims that msgvault is incapable
of implementing them:

1. **A standard, independent preservation package.** MBOX is canonical, not just
   an export; BagIt/Mailbag and message-level SHA-256 declarations are part of
   the archive contract, and verification does not require the database.
2. **Byte- and observation-centered custody.** ECT preserves original message
   bytes, records source observations, and distinguishes different content even
   when headers such as Message-ID match. It documents every allowed
   transformation, including the IMAP line-ending exception.
3. **Read-only historical-source handling.** Its local acquisition focus includes
   legacy email formats and partially recoverable stores. It explicitly accounts
   for exclusions, malformed inputs, interrupted work, and available provenance
   rather than treating a successful database import as the only outcome.
4. **Retained malware dispositions.** A positive scan routes a message to the
   infected MBOX; scanner errors and unscannable inputs are recorded and retained.
   The documented scanning path uses a run-owned embedded engine, not a resident
   mail-scanning service.

These distinctions are meaningful for researchers and long-term custodians.
Msgvault is the more comprehensive current personal information manager; ECT's
value is not a greater number of integrations or screens.

## Ideas worth adopting selectively

1. **A first-class backup/restore workflow (high value).** Borrow the separation
   between backup creation, integrity verification, inventory, and restore
   validation. For ECT, include the canonical bag, all operational databases,
   configuration, and other durable state. Retain MBOX/BagIt as the authority;
   treat any incremental database snapshot as a backup mechanism only. Define
   encryption, retention, privacy, and recovery limits before presenting it as
   protection.
2. **Clear, layered verification claims (low cost).** For every verification
   command and UI result, state exactly what was checked: bag/file fixity,
   message recovery, source coverage, scan status, or search-index completeness.
   Msgvault's explicit verification limitations are a good model for preventing
   overclaim; ECT's own layers should remain the actual contract.
3. **Readable source planning and review (moderate value).** Borrow a clear
   inventory/preview and reversible-review interaction for selecting sources,
   showing expected coverage and explaining excluded/unsupported items. Keep the
   source immutable and do not turn a preview into authorization to ingest into
   a real archive.
4. **Attachment-derived search (already identified ECT gap).** Msgvault's
   optional document/image discovery suggests a useful direction for ECT's
   planned PDF/Office text extraction. Extracted text and any embeddings must
   remain rebuildable derivatives with provenance; original attachments remain
   untouched. Start locally and make any external processing a separate,
   explicit consent choice.
5. **Structured, people-oriented discovery (selective).** Study its search
   filters, contact views, and evidence-linked relationship exploration. Adopt
   only pieces that improve ECT's existing email search/research workflow and
   preserve a clear link to underlying messages. Do not import a large people
   management subsystem merely to match the broader product.
6. **Provider breadth (evaluate separately).** Gmail/Graph OAuth and ongoing
   synchronization could address real ECT acquisition gaps, but would need ECT's
   own read-only authorization, source identity, completeness, checkpoint, and
   provenance guarantees. Reuse ideas and interoperability where appropriate,
   not an unreviewed provider client or database schema.

## What not to adopt

- **Do not move canonical mail into SQLite or replace MBOX/BagIt with
  msgvault's archive schema.** That would trade away ECT's independent
  preservation and verification goal for implementation convenience.
- **Do not mutate a source mailbox, delete messages upstream, or add remote
  deletion.** A staged confirmation interface cannot override ECT's source
  immutability rule.
- **Do not let Message-ID matching, survivor choice, or hard deletion merge
  distinct canonical bytes.** Duplicate review can be improved, but canonical
  identity and retained source observations remain ECT decisions.
- **Do not copy the full chat/calendar/meeting/contact scope, daemon/API/MCP
  architecture, or provider-dependent AI features by default.** Each expands
  maintenance and privacy obligations without strengthening ECT's core archival
  use case.
- **Do not treat a successful sync, backup, or verification as proof of complete
  source capture.** Provider limits, missing attachments, source parsing,
  restoreability, and index coverage require separate evidence.

## Primary references

- [msgvault README at reviewed commit](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/README.md)
- [msgvault source guide at reviewed commit](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/guides/sources.md)
- [msgvault architecture overview at reviewed commit](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/architecture/overview.md)
- [msgvault backup guide at reviewed commit](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/usage/backup.md)
- [msgvault verification guide at reviewed commit](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/guides/verification.md)
- [msgvault deduplication guide at reviewed commit](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/docs/usage/deduplication.md)
- [msgvault security policy at reviewed commit](https://github.com/kenn-io/msgvault/blob/3b9912c3631b515266d1131d1611d5abbb404f9b/SECURITY.md)
