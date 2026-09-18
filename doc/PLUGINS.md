<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Plug-ins

## Implemented CLI processor framework

API v2 runs both executable test plugins and production CLI processing.
Python plugins execute in the host interpreter; no Python plugin subprocesses
are created. The Rust PST importer remains an external executable. Use a fresh directory;
there is no requirement to migrate or preserve existing generated archive
formats. Source mail remains immutable.

All commands run through the Makefile:

~~~sh
make processor ARGS="--archive .tmp/processor-demo --plugin-dir tests/processing_plugins init"
make processor ARGS="--archive .tmp/processor-demo --plugin-dir tests/processing_plugins plugins"
make processor ARGS="--archive .tmp/processor-demo --plugin-dir tests/processing_plugins submit tests/data/credible_date_with_quoted_body.eml"
make processor ARGS="--archive .tmp/processor-demo --plugin-dir tests/processing_plugins run --max-jobs 1"
make processor ARGS="--archive .tmp/processor-demo --plugin-dir tests/processing_plugins run"
make processor ARGS="--archive .tmp/processor-demo --plugin-dir tests/processing_plugins status"
make test-processors
~~~

The plugins command prints manifests; status and run print typed JSON reports
with persistent invocation counts and timings. A failed run exits 1.
Ctrl-C stops CLI processing, retains unfinished jobs, prints statistics and exits 130.
Run with --retry to retry failed jobs while retaining successful checkpoints.
After changing manifests or entrypoint code, use reprocess explicitly:
old unfinished jobs are superseded, original inputs are queued under the new
registry fingerprint, and manual identity/tag data and invocation history remain.
An unchanged registry makes repeated submissions/reprocessing idempotent.

The registry validates local module:factory entrypoints without importing code,
normalized exact MIME types, required lower-rank subscribers covering the same
inputs, and acyclic type emissions. This initial serial dispatcher is a valid
rank-barrier implementation; concurrency is deferred. It releases typed
emissions/handoffs transactionally and blocks them until the parent job completes.
Part aborts retain sibling work; message aborts stop that message; import failure
leaves other jobs pending. Unsubscribed objects complete without discarding bytes.

Trusted processors execute synchronously through `process(item)` in the Python
host. `item.check_cancelled()` checks cancellation and the manifest deadline;
`item.remaining_seconds` supplies the remaining budget to blocking I/O. Long
loops must cooperate. The POSIX main-thread harness also uses an alarm. A late
result is rejected before configuration, queue or catalog publication. Arbitrary
native code that ignores deadlines cannot be force-stopped safely in process;
this interface is not a security sandbox. ClamAV uses its existing on-demand
native service and a bounded command invocation; the Python ClamAV plugin itself
runs in process. No persistent service is installed or enabled.

The fresh processing.sqlite3 schema is packaged separately under
processing/sql/V2__processing.sql; it does not enter the production catalog
schema directory.
It contains messages/occurrences, jobs/invocations, persons/aliases/addresses,
organizations/domains, dated affiliations, evidence, tags and manual decisions.
Overlapping affiliations are allowed. Nullable tag styles mean no override.
The harness snapshots input bytes into content-addressed objects/ files;
its admission identity is the raw digest, **not production message deduplication**.
Production admission uses normalized Message-ID plus raw SHA-256 and the existing
transactional MBOX publisher. Every source/attachment occurrence is retained.
Generated synthetic objects are working data, not canonical message records.

The processing package contains API models, registry validation, queue storage,
the dispatcher, production services and built-in processors. The invocation
carries application/archive contexts, the raw message and current content,
source/parent provenance and processing policy. Typed results request host
publication; plugins do not manipulate the live catalogs directly.

The tests invoke real plugins and CLI programs with SQLite and synthetic mail,
without mocks. They cover rank/abort behavior, deadlines, checkpoint recovery,
configuration, input integrity, deferred processing, identity edits, MIME
selection, attached messages and the real Rust PST adapter.

### Production CLI

~~~sh
make run ARGS="--archive .tmp/demo ingest --owner-names-file tests/fixtures/owner-names.txt --clamav --defer-content tests/data/three_messages.mbox"
make run ARGS="--archive .tmp/demo processors"
make run ARGS="--archive .tmp/demo processing-status"
make run ARGS="--archive .tmp/demo process --phase content"
make run ARGS="--archive .tmp/demo identities addresses --mailbox sender --start 2024-01-01"
make run ARGS="--archive .tmp/demo identities organizations --domain example.net"
make test-cli-processors
~~~

`ingest` finishes ingest/message jobs before running content. `--defer-content`
leaves content pending. `process --phase ingest|content|all` resumes stored jobs
without needing source files; continuing an unfinished source traversal still
requires repeating `ingest` with that source. `--max-jobs` bounds a content pass.
Failed work is retried; successful invocations are reused. Registry, processor
code, policy or effective settings changes queue a new generation automatically;
`process --reprocess` does so explicitly. Reprocessing filed messages starts at
message processing, retaining their original antivirus provenance. It never
unquarantines infected messages. Original source bytes, canonical MBOX files and
manual decisions are retained. Working `objects/` files, including synthetic
parts, are outside the canonical BagIt payload.

`identities` returns JSON address rows with canonical names, first/last use and
distinct message counts, or organization rows grouped by parent domain using an
offline Public Suffix List. Mailbox/domain/name and inclusive start/end filters
are independent. `rename-person`, `merge-person`, `rename-organization` and
`affiliate` use `--subject`, `--target`, `--name`, `--start`, `--end` as applicable;
edits and audit records commit immediately under the archive writer lease.
Signature addresses enter the evidence index before authoritative matching.
Heuristic signature evidence is not an automatic identity merge. Explicit
manual affiliations allow overlapping dates and open endpoints.

Known public providers such as Gmail, Outlook and Yahoo retain address evidence
without creating automatic institutional affiliations. Add domains to the local
set with `plugins.identity-evidence.provider_domains` (a list of parent domains).
Generation invalidation removes stale search rows and queues replacement work
in one rollback-journal SQLite transaction across the processing/search databases.
Searches cannot keep using the old body or attachment index after that commit.

HTML, RTF, text-index and identity-evidence processors default to at most 8 MiB
of input and derived UTF-8 output per part. Their `max_text_bytes` setting can
change that finite bound. Over-limit parts are explicitly skipped with a diagnostic;
canonical message and attachment bytes remain available. This bounds derived
conversion work; it does not impose a maximum canonical message size.

`item.archive.mailbox(year, role)` returns a typed filing destination, for example
`(2006, "Sender")`, `(2006, "Archive")`, or `("INFECTED", None)`. A `Filing` result
requests transactional publication of original bytes. Header, text, MIME
inventory, scan, identity evidence and child-message promotion results are typed.
The MIME extractor declares `emits_mime_parts = true` to dispatch normalized
actual MIME labels; it cannot emit framework-only labels through that permission.
Attached RFC 822 messages use inherited scan provenance, shared deduplication,
parent/MIME-path occurrences and the persisted attachment tag. The viewer and
incomplete-work dialog are integrated; the general tag editor remains future work.

The source/file acquisition interfaces remain the adapters before raw-message
dispatch. Their `PluginContext` exposes the same namespace configuration reads
and writes; acquisition writes commit synchronously. `plugins.pst` configures the
Rust executable, timeout and output bound. PST stdout is spooled privately under
`processing-pst/`; failure retains output, executable/source hashes, diagnostics
and an exit receipt. Complete records preceding the final uncertain tail can be
filed; an unsuccessful importer never marks source traversal complete. A rerun
deduplicates those earlier records. Successful spools are removed after filing;
receipts remain. Build the executable with `make pst-importer` or configure its
path. Native installer bundling is separate release work.

PST defaults are 60 seconds, 1 GiB stdout and 64 MiB diagnostics; settings are
`timeout_seconds`, `max_output_bytes` and `max_diagnostics_bytes`. Limits are checked
while running and after exit. The receipt records the actual reaped exit code and
observed byte counts. Retained oversized outputs are explicitly truncated to
bounded prefixes, with truncation flags; original PST bytes are untouched.

### Plugin-owned configuration

The invocation object binds settings to the plugin's stable manifest `kind`
(its registration name), independent of its human-readable `name`:

~~~python
settings = item.get_my_config()  # dict; archive overrides installation
archive_settings = item.get_my_config(scope="archive")
installation_settings = item.get_my_config(scope="installation")
item.write_my_config(archive_settings, scope="archive")
item.write_my_config(installation_settings, scope="installation")
~~~

Both configuration files contain a `plugins` mapping indexed by registration
name. For example, an archive override for the `identity-extraction` plugin is:

~~~yaml
version: 2
plugins:
  identity-extraction:
    languages: [en, fr]
    signatures:
      maximum_lines: 20
~~~

Archive settings live in `<archive>/config.yaml`. Installation settings use
version 1 and live in `config.yaml` beside the application's per-user
`preferences.json`, shared across archives. They do not modify the packaged
read-only `configuration.yaml`. The CLI accepts `--installation-config PATH`
before the subcommand for an explicit installation file, including fixture runs.
Absent namespaces/files yield empty dictionaries; invalid files fail visibly.

Effective settings recursively merge dictionaries. Archive lists, scalars and
explicit nulls replace installation values. Reads return defensive copies.
Writes **replace only this plugin's selected layer**, defaulting to archive;
writing `{}` clears that layer's overrides. To change one setting, read that
layer, edit it, then write it back. Writing the effective dictionary to the
archive intentionally pins inherited installation settings as archive overrides.

Writes are staged in the invocation and visible immediately to its later reads.
The parent persists them only after all subscribers at that rank succeed.
Timeouts, exceptions and part/message/import aborts discard that rank's staged
writes. The complete rank locks its target files, re-reads them and preflights
every namespace hash before any replacement. It preserves unrelated plugin
namespaces and archive owner/import settings. A durable transaction journal
commits the batch; individual files are atomically replaced. Interrupted
publication is completed from that journal before another plugin reads settings.
Recovery preserves unrelated edits and replays equal-value writes;
preflight conflicts fail the job so `run --retry` obtains fresh snapshots.
I/O failure after journaling retains completed invocations for publication retry.
Earlier successful ranks retain their committed writes if a later rank fails.

The configuration service is available to processors and source/file adapters.
Settings changes affect subsequent invocations; the production host fingerprints
effective settings, processing code and policy to invalidate completed derived
work. The registry fingerprint covers manifests and local Python code, including helpers.


## Proposed ranked processing graphs

![Proposed container, message and content processor DAGs](../website/static/images/processor-dag.svg)

The same graphic appears on the [website plugin page](../website/content/plugins.md).
This section describes the complete target architecture. The CLI framework above
implements production processing and GUI resume/picker controls. Remaining
contract gaps are identified below rather than implied by the diagram.

Source/container acquisition, including its file-parser subtree, supplies raw
messages to three processing pipelines:

1. **Ingest:** ClamAV runs at rank 1 on the entire raw message. A positive result
   writes through the INFECTED service and aborts downstream processing.
   The filing/handoff plugin runs at rank 2: deduplicate, durably publish the
   original bytes, and queue message processing.
2. **Message processing:** extract headers and store basic metadata in SQLite;
   a handoff plugin then queues content processing. Additional per-message
   subscribers can register independently.
3. **Content processing:** MIME extraction publishes typed body/attachment
   objects to the appropriate subscribers, including identity extraction.

The existing source/file layers precede these pipelines; they have not been
removed or renamed as message processing. Plugins receive one typed processing
object with application services, archive context, immutable content reference,
content type, message/parent-part identity, body/attachment scope, and synthetic
provenance.

Every plugin can access all headers and all content of its current message,
regardless of pipeline or subscribed type. Type and body/attachment selectors
control invocation, not access. Plugins do the work appropriate to their
pipeline. The filing plugin may read whatever it needs after ClamAV, retaining
current date/owner/autosave/deduplication rules; full metadata storage belongs
to message processing.

Dispatch follows a DAG of declared types and dependencies. Subscribers declare
a rank; all work at a rank completes before higher-ranked subscribers for the
same type run. Required cross-type outputs must also precede their consumers.
ClamAV rank 1 and filing/handoff rank 2 are ingest ranks; MIME extraction belongs
to content processing. Abort scopes are part, message, and import. Scanner timeouts default to 60 seconds and are configurable in TOML.
Invocation statistics include count, total time, shortest, longest, average,
errors, and timeouts. The CLI and About list registered processors by subscribed type. About does not
yet include source/file acquisition plugins or processor versions.

Publication uses a framework service selecting year/role, including the
INFECTED destination, and preserving transactions, locking, byte fidelity and
recovery. Quarantine records minimal message identity/provenance/scan outcome
without requiring header parsing, then stops the message's tree.

TOML declares body-only, attachment-only, or both. Synthetic objects inherit
scope and source provenance and are not published as canonical mail. Missing
plain-text body alternatives are synthesized from HTML preferentially, then
RTF. An unrelated text attachment does not suppress body conversion.

Attached emails become independent first-class message jobs with parent-message
and MIME-part paths. They have local body/attachment scopes and retain an
attachment-origin tag. Content processing submits each discovered child directly
to message processing, bypassing ingest and another antivirus scan. The child
retains the parent's scan provenance rather than claiming an independent scan.
The handoff uses the shared deduplication/publication service to establish the
child record and durable content reference. This is a new job root, not a
back-edge in one message's DAG. Attached and standalone occurrences use the standard normalized Message-ID plus
raw SHA-256 deduplication key, including the current missing-ID fallback. One
canonical message and its extracted metadata are shared; each occurrence has
its own source/provenance record, extended with parent-message and MIME-part
paths. The attachment tag means also observed as an attachment, not exclusively
attached. Child-byte recovery and resource-limit behavior are specified below.
The viewer shows origin paths; the attachment tag initially has a 5% gray background.

On archive opening, unfinished work prompts **Incomplete work**, with checked
**Continue ingest** and **Continue content processing** choices. The desktop bridge
uses the shared `IngestRequest` service, saved owner/scanner/plugin settings and
writer lease. The **Names and addresses** and **Institutions** controls query the
processor database; manual name/address decisions commit immediately and survive
replay. Institution membership is domain-based. About lists processor manifests
by subscribed MIME type. Authoritative matching is not yet connected. No persistent
“do not ask again” option suppresses unfinished work. Selecting only content processing runs eligible already-ingested messages even
when ingest remains incomplete; required message-metadata processing precedes
their content jobs. Selecting both completes ingest before deferred content
processing. Unchecking both opens the archive without starting work. Extraction
checkpoints distinguish plugin versions and completed, failed, and pending work;
manual identity decisions survive all extraction and matcher runs.

The full [tag editor is tracked in issue #119](https://github.com/simsong/email-collection-toolkit/issues/119).
Tag definitions have `tagid`, name, background color, text color, crosshatching,
and font; presentation fields permit NULL for no change. Definitions and
assignments are durable, not disposable search-index data.

### Registration and manifest contract

The implemented processor API is a versioned extension of the existing trusted
Python plugin system, not a new package installer. Existing source/file API v1
manifests remain supported through adapters. The new processor manifest uses
API version 2 and a `processors/<kind>/plugin.toml` directory beneath packaged
or explicitly trusted plugin roots. These fields are accepted by the processor loader; the production API v1
source/file loader remains separate until the next integration stage:

```toml
api_version = 2
plugin_type = "processor"
kind = "identity-evidence"
name = "Identity evidence extraction"
implementation_version = "1"
entrypoint = "plugin:create_plugin"
pipeline = "content"                   # ingest, message, or content
subscribes = ["text/plain"]
emits = ["application/x-mailarchiver-identity-evidence"]
rank = 10
scope = "both"                         # body, attachment, or both
timeout_seconds = 60
requires = []                         # required plugin kinds in this pipeline
```

Rank is a positive integer, independent of source/file recognition priority.
Timeout is a positive duration in seconds, defaulting to 60. Every processor
has a declared scope; raw-message/header invocations use the message's local
body scope. An attached email becomes its own message, with its own body and
attachment scopes and retained attachment-origin provenance.

Validate all manifests before importing plugin code. Reject duplicate kinds,
unknown phases/types of fields, missing required plugins, contradictory rank
dependencies, and cycles within a job graph. Freeze the registry for the run.
Use stable plugin-kind ordering within a rank; equal ranks permit concurrency
but do not promise it. Unsubscribed content is retained, not treated as an error.
The existing explicit trust, module-path confinement and no-archive-discovery
rules remain applicable. Trusted Python plugins are not sandboxed.

### One processing object and typed results

Each processor implements the conceptual interface
`process(item: ProcessingObject) -> ProcessingResult`. Both models and all
nested data records are typed Pydantic structures; arbitrary dictionaries are
not the internal API. The CLI framework implements the core classes; service capabilities and the
remaining provenance/deadline fields are added with production integration.

| Processing object field | Contract |
|---|---|
| `application` | Application/service context; no GUI-thread assumption |
| `archive` | Current archive and its controlled mailbox/catalog services |
| `job_id`, `pipeline` | Durable job identity and current pipeline |
| `message_ref` | Whole raw-message reference, all headers and content accessible |
| `content_ref`, `content_type` | Current immutable stream/reference and dispatch type |
| `part_path`, `scope` | Stable MIME-part path and local body/attachment scope |
| `provenance` | Source occurrence, parent message/path, and scan provenance |
| `synthetic`, `producer` | Derived status and generating plugin/version |
| `cancellation`, `deadline` | Framework cancellation and invocation deadline |

References support bounded streaming; emitting an object does not require
copying a message or attachment into memory. Application/service handles are
runtime capabilities, not serialized job payloads. Durable jobs store stable
references and reconstruct the processing object on resume. Accessors may
parse lazily; creating the raw object must not MIME-parse before ClamAV.

Dispatch uses normalized MIME types without parameters; charset and other MIME
parameters remain in typed content metadata. Framework types are explicitly
namespaced: `application/x-mailarchiver-raw-message`,
`application/x-mailarchiver-message-headers`, and
`application/x-mailarchiver-identity-evidence`. Actual parts retain their MIME
types, including `message/rfc822`. These framework labels are internal types,
not headers added to canonical messages.

A result contains emitted processing objects, typed diagnostics, and one
control outcome: continue, abort part, abort message, or fail import. Publications,
catalog changes and handoff requests use application services rather than
uncoordinated file/database writes. Plugins do not own threads or print reports.

### Dispatch, aborts and failure handling

For each input object/type, finish all subscribers at a rank before releasing
higher-ranked subscribers. Buffer emitted objects until the rank barrier has
resolved; downstream consumers cannot outrun a scanner or another aborting
subscriber. Cross-type required outputs and explicit dependencies also gate
dispatch. The strongest outcome wins: import failure, message abort, part
abort, then continue.

A part abort stops that part and its descendants, allowing unrelated sibling
parts to continue. At a raw-message root it stops that message. A message abort
cancels remaining work for that message; an import failure stops scheduling
new work and retains completed publications and safe checkpoints. Cancellation
of in-flight work must finish before releasing its protected resources.
Separate child-message jobs have their own abort scope once durably submitted.

A positive antivirus result is a handled message abort: commit quarantine and
its observation before stopping downstream work. A scanner error or timeout
is never a clean verdict. Record the failure, retain recoverable input and leave
the work retryable; stop normal filing of that input. Malformed MIME, decoding
failures and unscannable content must never silently discard original bytes.

In-process plugins must honor cancellation and pass their remaining timeout to
blocking operations. The host rejects late results and their staged writes.
It does not launch disposable Python processes or leave timed-out plugin threads
running in the background. The native scanner command and Rust importer have
bounded waits and are reaped before their resources are released.

### Archive services and durable handoffs

The mailbox service accepts a typed destination containing year and role,
including `(2006, Sender)`, `(2006, Archive)`, and `(INFECTED, None)`.
The Sender role maps to the existing Sent archive naming convention. Return a
controlled mailbox handle; publication remains behind the service so plugins
cannot bypass locking, journals, manifests, byte preservation or deduplication.

A publication/handoff operation establishes the message record, canonical
location, occurrence provenance and next-pipeline job together under the
archive's crash-recovery protocol. Never acknowledge source progress with an
unrecoverable gap between publication and queue creation. Replaying a handoff
must find the existing publication/job instead of duplicating either. SQLite
transactions alone do not make MBOX writes atomic; retain journal recovery.

The attached-message service extracts original embedded RFC 5322 bytes after
transfer decoding; it must not serialize a parsed message to invent those
bytes. Record parent message, MIME-part path and the child's SHA-256. When
exact extraction is impossible, retain the parent and diagnostic, leaving child
extraction incomplete rather than fabricating a canonical child. Deduplicate
through the ordinary admission service, add occurrence provenance, and queue
message processing directly. Do not enqueue ingest or ClamAV again. A child's
scan status records inherited provenance, never a fresh scan invocation.

Bound nesting depth, total expanded bytes and child count per root message.
Exceeding a bound records incomplete extraction with a retryable diagnostic;
it does not delete or truncate parent mail. Exact limits are implementation
configuration, not changes to content identity. Job identity and ancestry
checks prevent endlessly rediscovering the same child occurrence.

`plugins.mime` settings are positive integer `max_expanded_bytes` (134217728),
`max_parts` (10000), and `max_child_messages` (1000). Before releasing any
outputs, MIME extraction traverses the complete nested-message tree with one
budget. Expanded bytes count all intermediate split/decoded bytes written,
including repeated representations; this conservative bound can exceed the
final leaf-payload total. Part counts include multipart children, not just
attached emails. A limit raises a failed, retryable job before child publication;
raise the relevant setting and continue processing. Quoted-printable decoding
also uses bounded chunks. Depth limits also leave failed jobs.

### Persistent work, identity evidence and manual decisions

Scanner invocations persist typed clean/infected/not-scanned/unscannable/scanner-error
results with diagnostics and engine/signature versions (NULL if unavailable).
Errors and reported encryption/size-limit heuristics block filing and retain
raw input for retry. Version queries are cached per daemon configuration;
which unscannable conditions are reported depends on the daemon configuration.
Failed invocations may publish scan evidence only, never content or filing.


Attached-message promotion requires valid transfer decoding. Invalid base64
(including padding/trailing data), invalid quoted-printable escapes, and unknown
encodings leave failed extraction and retain the parent, without publishing a
child. Ordinary non-message MIME parts retain best-effort decode fallback.


MIME depth (default 40, `plugins.mime.max_depth`), attached-message depth
(default 20, `plugins.attached-message.max_depth`), and text size limits
(`max_text_bytes` on each text plugin) leave failed, retryable jobs. The GUI
offers continuation for these failures; increasing the configured limit and
resuming reprocesses the retained source. Limits never mark truncated work complete.

Persist job phase, message/part identity, plugin kind/version, relevant
configuration fingerprint, input digest, attempts, status and diagnostics.
Statuses distinguish pending, running, completed, aborted, failed and cancelled.
On restart, abandoned running jobs become retryable; successful unchanged work
is skipped. A plugin/version/configuration change invalidates its derived work
and dependent outputs without rewriting canonical messages or manual decisions.
Synthetic content can be regenerated from its producer and original reference.

The archive-open prompt covers incomplete message jobs as prerequisites of
content processing as well as unfinished ingest. Checkpoint completed work
per plugin and input; pause at safe publication boundaries. A failed prerequisite
blocks dependent work and remains visible rather than being marked complete.
No background run starts merely because a plugin was discovered.

Identity extraction initially supplies signature evidence, including addresses
absent from headers. Store those as unresolved addresses before authoritative
matching; count signature occurrences separately from header message counts.
Group subdomains under their registrable parent domain while keeping public
mail providers distinct from institutional affiliation evidence. Person-to-
institution links retain dated, potentially simultaneous affiliations.
Manual identity edits save immediately and remain authoritative across
extraction and matcher reruns; extracted suggestions retain provenance.

Tags have stable IDs and names, plus nullable background/text colors,
crosshatching and font. NULL means no style override. The initial attachment
tag uses a 5% gray background (`#f2f2f2`); the future editor and multi-tag display
rules are tracked in issue #119. Tag definitions, assignments and manual
decisions need durable backup/recovery beyond rebuilding a search index.

### Statistics, About and implementation acceptance

Use monotonic elapsed time per invocation. Count failures, aborts and timeouts
as invocations; accumulate count, total duration, minimum and maximum. Average
is total/count, or unavailable with no invocations. Reports show zero-count
plugins without misleading duration values. Aggregate safely across workers
and print the report on success, cancellation or handled import failure.

About groups the frozen registered processor list by subscribed MIME/framework
type, showing pipeline, rank, scope, version and timeout. Also show source/file
plugins and explicitly identify their acquisition role. Deferred content work
and scanner failures must be visible in progress/status reporting.

Implementation acceptance must exercise real behavior through Makefile targets:
rank and dependency ordering; each abort scope; timeout cancellation without
late writes; original-byte quarantine; crash/replay between publication and
handoff; content-only resume; attachment deduplication and provenance; nested
message bounds; body versus attachment selection; HTML-before-RTF fallback;
synthetic content exclusion from canonical mail; durable manual edits; and
accurate statistics. Use fixtures and the existing on-demand ClamAV EICAR test.
The processing framework and GUI integration are implemented and tested.
Remaining implementation gaps in this contract are separate signature/header counts, complete
About inventory, and timeout/empty-sample statistics. Exact API fields and
cancellation states must also be reconciled with the public models. These gaps
are not claims that the entire framework remains unimplemented.

## Implemented ingest plugins

Before the API v2 processor trees, Email Collection Toolkit implements the two
API v1 acquisition layers described below. They are deliberately separate from the planned geography data
and visualization extension points; an installed ingest plug-in cannot register
a graphical menu or render a visualization.

Email Collection Toolkit has two independent generator plug-in layers:

1. a **source plug-in** enumerates mail containers and streams mail objects from
   a source system; and
2. the built-in local source delegates each recognized filename to a
   **file-parser plug-in**.

The planned [ingest executable protocol](PST_DUAL_READER.md) is a subprocess
adapter into these layers: filename input, mboxrd stdout, stderr diagnostics,
and separate `X-Imported-URI`, `X-Importer-Name`, `X-Importer-Version` fields.
The [MCT Importer API 1.0](MCT_IMPORTER_API.md) Rust generator and validator are
implemented, as is the PST adapter and its CLI archive-host integration. All added fields remain in
h2; see [added headers and integrity](INTEGRITY_CONTROLS.md#headers-added-by-mail-archiver-and-ingest-executables).

Plug-ins do not create threads, render status, scan messages, open the archive
catalog, deduplicate mail, or publish canonical MBOX. The framework owns those
operations. This keeps the same source plug-in usable with one worker or many
workers and with either the terminal dashboard or redirected logging.

The implemented flow is:

```text
packaged + explicitly trusted plug-in directories
    |
    v
validate every plugin.toml, then import code and freeze registries
    |
    v
SourcePlugin.discover(SourceSpec)
    -> MailContainer | ProgressEvent | SkippedInput
    |
    | framework snapshots, deduplicates, verifies stable inventories,
    | and fairly orders concurrency keys
    v
framework integrity plan
    |
    v
SourcePlugin.messages(MailContainer, resume_cursor)
    -> MailObject | ProgressEvent
    |
    | local source delegates to FileParserPlugin.messages(...)
    v
parse -> deduplicate -> ClamAV -> publish -> integrity completion and checkpoint
```

## API v1 framework responsibilities

The framework owns:

* plug-in discovery, manifest validation, deterministic ordering, and registry
  freezing before inventory;
* source selection and rejection of ambiguous matches;
* inventory, the global worker pool, cancellation, and per-source concurrency
  limits;
* status aggregation and all terminal or log output;
* execution and persistence of source-selected integrity controls;
* SHA-256 of each transferred RFC 5322 message, deduplication, and ClamAV
  routing;
* catalog transactions and source-integrity checkpoint commits;
* canonical MBOX publication; and
* the archive format's fixed BagIt, Mailbag, `h1`, `h2`, and `h3` integrity
  controls.

A plug-in is read-only with respect to its source. It yields typed values and
may read its source to produce those values. It must not print, start workers,
or write to the archive.

## API v1 versioned contracts

The public API is in `mailarchiver.plugin_api` and currently has API version 1.
All boundary values are immutable Pydantic models with unknown fields rejected.
The principal models are:

* `PluginManifest` and `PluginCapabilities`;
* `SourceSpec`, `SourceReference`, and `FileProbe`;
* `MailContainer`, `MailObject`, `ProgressEvent`, and `SkippedInput`;
* `IntegrityDecision` and `IntegrityEvidence`; and
* `ArchiveReference`.

`MailContainer` is a bounded scheduling and recovery unit. It contains a stable
`work_id`, a source reference, optional message and byte estimates, a
`concurrency_key`, and optional `plugin_data_json`. The last field is private to
the plug-in and must contain only a plug-in-specific Pydantic model serialized
as JSON. It must never contain credentials. A work ID is stable within its
source account; the framework scopes it by plug-in kind and source ID.

`SourceReference.provenance_json` may carry a source-specific Pydantic model
containing non-secret provider provenance. The framework stores it with the
source container; access tokens and credentials are forbidden.

`MailObject` is one streamed message. `raw` is the provider's original RFC 5322
representation. `work_id` binds it to its container, while `cursor` is an
opaque source-native record locator. Optional message and byte fields report
progress without coupling the generator to the status display. The framework
stores the opaque cursor and, when it is numeric, also stores a sortable source
position. A provider may supply a message-specific `source_date_utc` only as a
fallback when neither `Date:` nor `Received:` resolves a date. The typed field
must be timezone-aware and is normalized to UTC; when used, the catalog records
`date_source='source-fallback'`. Header consensus takes precedence over this
field; the previous-message and local path-year fallbacks follow it. The
framework applies the ingest run's configured earliest plausible year to every
candidate date regardless of its source.

`ProgressEvent` is data, not output. The framework sanitizes and renders its
phase and byte or message progress in the assigned worker row. Unknown-byte
sources use completed-container counts for overall percentage. A `SkippedInput`
similarly identifies an unrecognized item; the framework prints each skipped
path and reason once.

## Source plug-in contract

A source plug-in implements:

```python
class SourcePlugin(ABC):
    capabilities: PluginCapabilities
    integrity_controls: SourceIntegrityControls

    def recognizes(self, source: SourceSpec) -> bool: ...

    def discover(
        self, source: SourceSpec
    ) -> Iterator[MailContainer | ProgressEvent | SkippedInput]: ...

    def messages(
        self, container: MailContainer, resume_cursor: str | None
    ) -> Iterator[MailObject | ProgressEvent]: ...
```

The framework requires exactly one source plug-in to recognize each command-line
source. It writes discovery output to a temporary SQLite work snapshot before
ClamAV or archive publication. Duplicate `(plugin kind, source ID, work ID)`
containers are scheduled once; conflicting definitions are fatal.

For `stable_inventory=True`, the framework calls `discover()` a second time and
compares the complete container definitions before starting workers. For
`stable_inventory=False`, it calls `discover()` exactly once and processes that
captured worklist even if the live provider changes afterward. The snapshot is
read in round-robin concurrency-key order so a long account inventory cannot
hide later accounts behind its own limit.

`PluginCapabilities.max_concurrency` is enforced by the framework for each
container's `concurrency_key`; a plug-in never owns a private thread pool. For
example, a provider can use an account ID as the key so several accounts run in
parallel while requests to one account remain bounded.

One source-plug-in instance and its integrity-control instance are shared by all
framework workers. `messages()` and integrity-control methods must therefore be
reentrant or protect mutable provider-client state. The framework rejects a
`resume` decision unless `resumable=True` and a cursor is present.

## Local source and file parsers

The production `file-folder` source recursively walks local files in sorted
directory and filename order. It probes each regular file against the frozen
file registry. No match yields a `SkippedInput`. Multiple packaged matches are
resolved by manifest priority; any overlap involving an external plug-in is a
fatal ambiguity. Known incomplete or malformed formats remain fatal rather than
being reported as harmless skips. Exact-basename `Info.plist` and
`table_of_contents` plus case-insensitive `.toc` mailbox metadata are silently
omitted before recognition.

For every match, the source yields a `MailContainer` holding an opaque serialized
`LocalContainerData`. When a worker consumes it, the local source calls the
selected file parser and converts its records to source-neutral `MailObject`
values.

Recognition receives the filename in `FileProbe`; record generation receives
the resulting `MailContainer` rather than a second bare filename. That small
difference from a filename-only interface keeps the framework-selected source
identity, provenance, estimates, and integrity boundary attached to the work,
and lets the same scheduler handle local files and virtual provider containers.

The packaged file parsers are (PST/OST details and limits are in
[PST_IMPORTER.md](PST_IMPORTER.md)):

| Kind | Recognition | Behavior |
|---|---|---|
| `pst` | PST header classification | Rust extraction; optional redundant in-process libpff pass |
| `ost` | OST header classification | In-process libpff cache extraction |
| `emlx` | `.emlx` suffix | Reads the declared RFC 5322 length; rejects partial EMLX |
| `babyl` | case-insensitive `BABYL OPTIONS:` signature | Streams Emacs RMAIL Babyl records, including extensionless files |
| `mbox` | initial `From ` separator | Streams MBOX records with numeric offsets and safe append resume |
| `message` | `.eml` suffix or a direct `cur`/`new` child in a `cur`/`new`/`tmp` Maildir, after higher-priority packaged formats | Streams one RFC 5322 message |

Packaged precedence is EMLX, Babyl, MBOX, then `message`. In particular, an
MBOX envelope signature wins when a one-message MBOX file resides under a
Maildir `cur` or `new` directory. Any overlap involving an external file
plug-in remains a fatal ambiguity.

Babyl parsing handles LF and CRLF containers, uses the original-header block
when present, falls back to visible headers when needed, and omits Babyl labels
and redundant visible-header metadata from the yielded message. A `0x1f` end
marker before the first record represents a valid empty mailbox. It never
changes the source file.

The older `FileParser`, `SourceFile`, `SourceMessage`, and explicit registration
functions remain as a compatibility facade for the built-in physical parsers
and direct tests. Production discovery is manifest-driven and frozen before
workers start.

## Integrity controls

Integrity has three distinct layers.

### Source integrity

Every source plug-in supplies `SourceIntegrityControls`:

```python
class SourceIntegrityControls(ABC):
    control_id: str

    def plan(
        self,
        container: MailContainer,
        prior: tuple[IntegrityEvidence, ...],
    ) -> Iterator[IntegrityDecision | IntegrityEvidence | ProgressEvent]: ...

    def complete(
        self,
        container: MailContainer,
        planned: tuple[IntegrityEvidence, ...],
    ) -> Iterator[IntegrityEvidence | ProgressEvent]: ...
```

Planning emits exactly one `read`, `skip`, or `resume` decision. The framework
records the attempt before message processing and marks it complete only after
`complete()` succeeds. A failed attempt therefore cannot replace the last safe
checkpoint. Evidence is stored in `source_integrity_checks` and
`source_integrity_evidence`; cryptographic evidence must name its algorithm,
while provider version tokens, immutable identifiers, cursors, and metadata
must not claim a hash algorithm.

The framework consumes `complete()` outside the publication lock, forwarding
each `ProgressEvent` as it is yielded. This permits source hashing or provider
I/O to proceed independently across workers. After the generator finishes and
its evidence is validated, the framework acquires the publication lock only to
persist the final evidence and completed checkpoint atomically.

The local source control calculates complete and prefix SHA-256 evidence. A
matching complete digest skips an unchanged file. A grown MBOX resumes only
when the old-length prefix digest matches and the next bytes form an MBOX
message boundary. Truncated files, changed files, unsafe appends, Babyl, EMLX,
and single-message files are fully read. Completion checks the discovered file
size and nanosecond modification time before committing evidence.

Provider controls use provider semantics. Gmail history IDs, IMAP UIDVALIDITY
and UIDs, and Microsoft Graph ETags, change keys, or delta links are version or
cursor evidence—not cryptographic fixity.

API version 1 commits provider integrity evidence only after the whole bounded
container completes. It does not advertise or silently discard per-message
checkpoints. Provider containers must therefore be replayable and reasonably
bounded; interruption re-reads the incomplete container and normal
deduplication handles already committed messages.

### Message-transfer integrity

The framework computes SHA-256 over every `MailObject.raw`, stores it on the
source observation, and uses `(Message-ID, SHA-256)` for deduplication. This
binds a source record to the archived message but does not replace a container
or provider integrity control.

### Archive integrity

Source plug-ins cannot select, weaken, or disable archive integrity controls.
`MailbagArchiveIntegrityControls` wraps the current archive implementation. It
initializes BagIt declarations and the standalone verifier, publishes the
Mailbag metadata and payload/tag manifests after canonical MBOX publication,
and exposes independent verification. The canonical `h1` complete-MBOX, `h2`
recovered-message, and `h3` semantic-message controls are described in
[INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md).

## Directory discovery

Packaged plug-ins live below:

```text
mailarchiver/plugins/
  sources/<kind>/plugin.toml
  files/<kind>/plugin.toml
```

Additional roots are loaded only when explicitly named with repeatable
`ingest --plugin-dir DIRECTORY` options. Each such root has the same `sources/`
and `files/` layout. Mail source trees, the current directory, environment
variables, and the archive directory are never searched implicitly.

Directory membership authorizes Python code execution. Only use
`--plugin-dir` for code you trust.

A manifest contains:

```toml
api_version = 1
plugin_type = "source"       # or "file"
kind = "example"
name = "Example source"
implementation_version = "1"
priority = 100
entrypoint = "plugin:create_plugin"
```

For external plug-ins the entry module must resolve inside that plug-in's own
directory. Absolute paths, `..`, escaping symlinks, missing modules, invalid
API versions, type/directory mismatches, and duplicate `(plugin_type, kind)`
pairs are rejected. All manifests are parsed and validated before any external
module is imported, so one invalid manifest prevents all dynamic code execution
for that startup.

After validation, candidates are sorted by `(priority, kind)`. File plug-ins
are instantiated first. Source factories may accept a read-only `PluginContext`
containing the frozen file registry, which is how the local source delegates to
directory-discovered file parsers. Zero-argument factories are also supported.
The completed source and file registries are immutable.

## Built-in source status

| Kind | Status |
|---|---|
| `file-folder` | production |
| `gmail` | reserved stub; no account access |
| `imap` | reserved stub; no server access |
| `o365` | reserved stub; no Microsoft Graph access |
| `microsoft-exchange` | reserved stub; no Exchange access |
| `stdin` | reserved stub; intended for NUL-delimited records |

The reserved plug-ins recognize their explicit URI schemes and fail clearly.
They do not imply that remote or stream ingest works. A production adapter must
retrieve original raw MIME, define stable source identities and hierarchy,
implement provider-appropriate container integrity and resume evidence, and
pass provider-local integration tests.

The generic provider path itself is implemented and tested: a dynamically
loaded source can emit a virtual container and opaque cursor, use a provider
version-token control, run through the framework worker/status/ClamAV/archive
pipeline, and skip the unchanged container on a later run.

## Catalog representation

The catalog retains the historical table names `source_volumes` and
`source_files`, but they represent source origins and containers. A local row
has `path_kind='file'`; a provider row has `path_kind='provider'`.
`source_plugin`, source-scoped `work_id`, the source-native path or ID,
per-container display/hierarchy/provenance metadata, and the opaque observation
cursor preserve enough information to interpret provenance without reopening
the source. `hierarchy_path` drives mailbox-tree filtering. Opaque cursors have
a nullable numeric projection, so a provider cursor is not confused with byte
offset zero. Provider credentials are never catalog metadata.

Source integrity attempts and their ordered typed evidence are append-only per
run. `source_files.sha256`, `checked_at`, and `completed_run` remain local
display/cache fields; skip and resume decisions use only the most recent
completed typed integrity check.

An optional local file-parser `configuration_fingerprint()` returns a stable
SHA-256 of the reader version/settings that affect extraction. The local source
stores it in container metadata and integrity evidence under
`local-parser-settings-v1`. Missing or changed fingerprints force a fresh read,
even when file bytes match. This supports turning on the developer-only
[Redundant PST Import](PST_IMPORTER.md#redundant-pst-import-testing-option) option
after a Rust-only import. Other file parsers retain source-only checkpoints.
OST uses the in-process `ost`/libpff file plugin and an Outlook cache relationship.
Its receipts distinguish extracted cache contents from server completeness.

## Planned geography data providers

Geography refresh is a data-installation pipeline, not an ingest plug-in. It
will download versioned bulk reference datasets, validate their manifests, and
atomically replace the per-user geography database. Its sources include US ZCTA
geography and university main-campus/domain data. It does not submit contact
data to a public lookup service. An optional archive snapshot is an explicit
copy/read choice and remains under archive migration control. The data-provider
interface has not yet been defined; it must not be represented as API v1.

## Planned visualization plug-ins

Visualization plug-ins are a third, future architecture, separate from source
and file-parser plug-ins. The initial API is intentionally limited:

* a plug-in may register one or more host menu entries;
* the host supplies controlled, read-only access to the selected archive
  database;
* the host supplies a visualization canvas or window; and
* a visualization may export HTML or PDF.

It will not initially offer arbitrary user-interface extensions, credentials,
source-mail mutation, or unrestricted archive writes. The API version,
manifest shape, database capability boundary, canvas lifecycle, and export
contract remain to be designed in [issue #81](https://github.com/simsong/email-collection-toolkit/issues/81).
Until then, no visualization plug-in directory or manifest is supported.
