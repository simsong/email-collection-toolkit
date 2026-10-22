<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Processing requirements

## Working-object lifetime

All production ingest and processing operations, including IMAP, shall release
cached raw messages after their canonical MBOX publication and catalog/processing
SQL commits, without waiting for the import to finish. Deferred processing shall
reconstruct raw input from verified canonical bytes. Pending, running and failed
jobs shall retain derived inputs and successful outputs needed for rank retry.
Keep unpublished originals and originals whose canonical MBOX copy cannot be
verified. Standalone processor harnesses shall keep their original snapshots
because they have no canonical MBOX fallback. Collection holds the writer lease.

Completed and aborted job records are audit history: their content hashes/paths
need not name resident working files. Reprocessing shall reconstruct missing raw
inputs from SHA-256-verified canonical MBOX records and regenerate derived parts;
it shall preserve manual decisions and antivirus provenance. Never collect through
symlinks or delete unknown files. Cleanup shall not modify source mail or canonical
payloads. Validate with `make test-processing-cleanup`.

Host-scanned ingest retries may reuse saved `clean` or `infected` verdicts for
verified unchanged bytes. Saved `scanner-error`, `unscannable` or `not-scanned`
evidence must trigger a fresh scan under the current scanning policy, rather
than permanently replaying an old failure or bypassing required scanning.
Retain earlier failed invocation records and the unpublished original bytes.
Producer scan headers and explicit unscanned-import policy remain authoritative.
When both ingestion and unbounded content processing are enabled, recover eligible
archived message/content jobs before pending ingestion or source traversal. A
failed pending ingest must not starve the existing archive's search reconstruction.
Explicit deferred-content and content-job limits retain their existing behavior.
Failed archived extraction remains durable and reportable, but the preliminary
recovery pass must not prevent unrelated new mail from reaching canonical storage.
Independent healthy archived jobs must continue after a failed job. Descendants
of a failed prerequisite remain blocked; explicit `fail-import` and cancellation
still stop the run.
Validate with `make test-ingest-recovery`.

Current subsystem contract and acceptance boundaries. This is not a claim of
platform/release acceptance. Deferred features live in [plans](../plans/README.md).

## Contacts and geographic reference data

### CLI processor framework (first stacked PR)

The framework shall be testable without GUI windows or production mail plugins,
using real in-process test plugins through make processor and
make test-processors. It shall validate manifests before executing code,
enforce rank barriers and type dependencies, preserve sibling work after a
part abort, and persist invocations and handoffs for interruption recovery.
Failed prerequisites must block queued downstream work across restarts.
Python processors shall run in process. Plugins must honor cooperative deadlines
and bound blocking I/O; timed-out results must not be committed. The Rust PST
importer is the external plugin executable.
Input content hashes shall be verified before dispatch. Explicit reprocessing
after registry changes shall retain manual identity decisions and audit history.

This redesign may change all generated on-disk formats; no compatibility
migration is required. A fresh schema shall include authoritative persons and
aliases, addresses, organizations/domains, dated simultaneous affiliations,
evidence, durable tags and manual decisions. Source mail remains untouched.
The framework schema must be packaged separately from production catalog
schemas; its creation must neither alter an existing catalog nor weaken its
schema validation. CLI cancellation must finish the active invocation and release the
writer lease and permit recovery without repeating completed invocations.
The framework harness uses copied fixture bytes and raw-digest identity;
production deduplication and import now use the same dispatcher. GUI pickers and the incomplete-work dialog shall use the same durable queues and identity tables.

Each processor shall receive its own configuration dictionary, identified by
its stable manifest kind. Archive settings override installation settings;
nested mappings merge recursively, while lists, scalars and explicit nulls
replace inherited values. Plugins can read either layer or their effective
settings and replace their own layer in either scope. Writes from unsuccessful
ranks must not be published. Persistence must preserve unrelated settings,
reject stale conflicting writes, support idempotent recovery and leave malformed
configuration untouched. Real-worker and CLI tests exercise these requirements.
All namespaces in a rank must be checked before any configuration replacement;
interrupted multi-file publication must recover before settings are exposed to
plugins. Missing or unreadable input objects must become reportable failed jobs
and permit retry after repair. Manifest types require two nonempty ASCII MIME
tokens. Inventory sizes and SHA-256 digests apply to 7z members as well as ZIP.

### Production CLI processor and identity integration

During dispatch, ProcessingObject.job_id exposes the durable SQLite job ID;
retries retain it and new emissions/handoffs receive their own IDs. Provenance
uses source_metadata, parent_message_id, part_path and scan_provenance. Plugins
use check_cancelled() and remaining_seconds; cancellation leaves resumable work
within pending/running/failed statuses rather than inventing a cancelled state.


For persistent attempt counts, timing and error reporting, follow the [processor contract](../PLUGINS.md).


For About inventory discovery and compatibility, follow [PLUGINS.md](../PLUGINS.md).


Keep header and signature counts separate as defined in [PLUGINS.md](../PLUGINS.md).


Persist scanner evidence and retryable failures according to [PLUGINS.md](../PLUGINS.md).


Promote attached messages only under the transfer-validation rules in [PLUGINS.md](../PLUGINS.md).


Apply the configured MIME, attached-message and text limits in [PLUGINS.md](../PLUGINS.md); retain failed, retryable jobs.

Before releasing MIME outputs, bound cumulative intermediate split/decoded
bytes, multipart children and attached messages across the entire nested tree.
Configure positive integer limits under `plugins.mime`; defaults are 128 MiB,
10000 parts and 1000 attached messages. Limit failures retain the canonical
parent and a retryable failed job. Decoders must bound reads as well as writes.
Synthetic fixtures test cumulative nested limits, retry and QP chunk boundaries.

The proposed ranked publish/subscribe processor DAGs, handoff plugins,
incomplete-work prompt, scanner timeout/statistics, synthetic-part provenance,
and first-class attached-message handling are specified in
[PLUGINS.md](../PLUGINS.md#processing-graph-design-and-deferred-capabilities). CLI and GUI processing shall use the same services, writer lease, and saved import policy. Manual decisions must survive reruns. Attached messages must retain
parent paths and an attachment tag, initially displayed with a 5% gray background.
The future SQLite-backed tag editor is tracked in issue #119; nullable style
attributes mean no change. The three pipelines are ingest (ClamAV then filing/handoff), message processing
(header metadata then content handoff), and content processing (MIME dispatch).
Content-only resumption may process already-ingested messages while ingest is
incomplete. Attached occurrences use standard deduplication, retaining separate
provenance records linked to shared canonical content and message metadata.
All plugins can access every header and all content of their current message;
the filing plugin may read what it needs after scanning. Discovered child
messages return directly to message processing without another antivirus scan,
retaining parent scan provenance and using shared deduplication/publication
services for the child record and content reference.

### Desktop processor integration

Opening an archive with pending, failed, or interrupted processing shall show
**Incomplete work**, with **Continue ingest** and **Continue content processing**
checked. Later, or clearing both choices, opens it without starting work; the
prompt returns on the next opening until work finishes. Source traversal resumes
from its original roots; rootless content runs must not hide interrupted imports.
Content-only processing must work without the source being available. Missing
saved policy requires an explicit File → Import instead of guessing scan settings.
Background processing must retain the GUI's stop/quit and single-writer safeguards.
Pending work alone must not trigger a quit warning. Content-only jobs stop and
checkpoint on quit without an ingest warning. Active ingest still requires stop
confirmation. Quit must not block the Cocoa event loop while waiting for workers;
workers must skip final UI refresh during shutdown. Ctrl-C requests the same
bounded shutdown without a confirmation dialog in the GUI. CLI Ctrl-C retains
its existing graceful checkpoint behavior without the GUI's forced-exit deadline.

Matcher matrix cells use two-point vertical padding, black text and column
headings, and dark supporting text. Live pickers have no Archive identities badge.
The main-window resume action is labeled **Continue Processing**.

Archive-backed name and institution windows shall show header and signature
addresses, date-filtered statistics, and distinct message counts per group. Name
moves, separation and renames save immediately under the writer lease. Institution
membership follows parent domains; institution names can be edited. The synthetic
prototype retains its session-only undo; live edits are durable and do not expose
that prototype reset/undo. Authoritative matching remains disabled until its
production algorithm is connected. About lists registered processors by input
MIME type, with pipeline, rank, scope and timeout. Attached message rows show an
attachment tag on a 5% gray background; the viewer shows the parent and MIME path.
`make test-gui-processing` exercises these services and shipped pages headlessly
against actual synthetic archives, without native windows or mocked services.

### Synthetic matcher window prototype

A reusable matcher window shall show canonical entries with large disclosure
triangles and child email addresses with separate first-use, last-use, and
message-count columns. Each column header toggles ascending/descending sorting
of both groups and their children while preserving the hierarchy. Group counts
must deduplicate message identifiers across addresses rather than sum counts.
Case-insensitive substring filters cover mailbox before `@` and domain after
`@`; both must match the same address. There is no full-email search box.
Optional start/end dates restrict actual message observations inclusively;
blank bounds are unrestricted. Recompute first use, last use, and counts within
the selected period, omitting addresses without matching messages. A reversed
range displays an error and no results. Retain headings only for matching
children and show visible/total address counts when filtered.
Dragging a canonical row moves all of its addresses, including filtered-out
children, beneath the destination canonical entry. Dragging a child moves only
that address. Moves retain all observations, even outside the current date
range; empty source groups disappear. Support a keyboard-accessible destination
picker, separating an address into its own entry, and undoing moves or a matcher
batch. Name and institution subclasses share these interactions but maintain
independent grouping state. Names disclose alternative email addresses;
institutions disclose their member addresses. Unassigned personal addresses
must not imply an institutional affiliation.
The prototype uses fictional `.test` addresses and session-only state. Matcher
buttons explicitly demonstrate predefined matches; authoritative algorithm and
database connections remain future work. No archive or preferences may be
opened or changed by this demo.

### Catalog-derived Contacts

The CLI and policy below are implemented; the Contacts window and geography
features remain planned.

Contacts are address-level records derived from `From`, `To`, `Cc`, and `Bcc`
headers; they are not authoritative People records. Each address occurs at
most once per message in all-header counts and date ranges. The Contacts view
shall offer a default-checked **Meaningful** filter. Meaningful means a direct
To or outgoing Bcc recipient of owner-sent mail, or an incoming sender where an
exact configured owner address occurs in `To`. Cc recipients are not
meaningful; multiple To recipients are. A mailing-list message counts only
when that direct-owner-in-To condition is met.
The include/exclude owner rules classify ingest; a separate reviewed exact
owner-address set for contact reconciliation remains planned;
meaningful-contact semantics use those exact addresses, never a name fragment.
The read-only `human-contacts` command shall provide this initial address-level
projection in table, TSV, and JSON forms before the Contacts window exists.
It opens the catalog read-only using a platform-correct file URI, including archive
paths with spaces, Unicode, and URI-sensitive characters; it never creates a
missing catalog. It shall accept a reusable owner-alias file whose values are separated by newlines,
commas, or semicolons; blank lines and comment lines are ignored. Aliases resolve
only to catalogued Sent sender addresses, and those resulting exact addresses
drive the meaningful-contact predicate.
It shall suppress mailing-list, automated-service, and malformed identities
using a versioned, explainable packaged policy. The human-contact local-part
limit is 48 characters and is configurable; the RFC address limit is not itself
a claim that every shorter address is human.
Classification reasons distinguish invalid-domain rules from invalid-local-part
rules; when both match, the local-part reason takes precedence. An empty domain
is `invalid-domain`, after evaluating local-part rules.
The packaged `contact_filters.yaml` may be copied to an archive root. Its
required `mode` is `replace` for a complete replacement policy or `extend` to
add only rule lists to the packaged policy. Extension preserves packaged order,
appends new rules, and removes duplicates; scalar thresholds remain packaged.
A malformed archive copy shall fail the command rather than silently changing
its classification. Validate every regex when the policy loads, including unused
rules and empty catalogs. YAML and regex errors identify the policy file and
offending rule without a traceback, partial output, or archive writes.

## Production processing acceptance

CLI `ingest --defer-content`, `process`, `processing-status`, `processors` and
`identities` shall exercise the production framework without GUI popups. Header
addresses are available after ingest; signature evidence, text/attachment indexes
and attached messages are resumable content work. HTML takes precedence over RTF
when synthesizing absent plain-text bodies; plain attachments do not suppress
body synthesis. Mailbox/domain/date picker filters use distinct message counts.
Manual names, address merges and simultaneous dated affiliations survive replay.
The PST file adapter shall invoke the real Rust importer, retain bounded failed
output and provenance, withhold its uncertain final record on failure, and never
mark partial extraction complete. `make test-cli-processors` exercises these
requirements with minimal RFC 5322 and public PST fixtures.

A positive antivirus verdict must publish to INFECTED even when header/date
parsing fails. Quarantine may use a clearly labeled unknown-date placeholder for
required catalog/envelope fields, with a metadata defect; it must not treat that
placeholder as an observed date or run downstream content/identity processors.

HTML, RTF and text processors must bound their derived input/output, reporting
over-limit parts while preserving the original message. Invalidation must remove
obsolete body/attachment search results atomically with queuing a new generation,
including when replacement processing is deferred or fails. Known public mail
providers must retain address evidence without creating automatic institutional
affiliations. PST timeout/limit receipts must retain the actual reaped exit code,
observed sizes and truncation flags; both live and post-exit output sizes are checked.

## Organization-domain evidence

Organization-domain evidence uses the bundled ICANN Public Suffix List offline,
including longest-match, wildcard and exception rules, with IDNA normalization.
Private suffix entries remain excluded, matching the prior tldextract policy.
No Requests-based fetching or public-suffix network update occurs at runtime.
