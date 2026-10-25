<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Processing implementation

`__main__.processor_statistics_table` renders the end-of-import stderr summary
from the existing typed statistics report. Counts and second timings align in
columns; the title identifies their cumulative archive scope. The JSON output
of `processing-status` remains unchanged.

## Working-object lifetime

`runtime.run` prepares interrupted checkpoints once. The dispatcher advances one
generator per active message and submits its next invocation to a bounded
`ThreadPoolExecutor`. Workers return typed results from isolated temporary
workspaces. A single coordinator consumes completed futures, retains output
bytes, commits checkpoints/configuration/catalog/search changes, and advances
the generators. Workers never access the shared SQLite connections. At most one
job per message runs at a time; parent/rank dependencies remain ordered. Queue
selection stays streaming and the coordinator owns the global job budget.
Failure/cancellation stops new claims while active work is checkpointed.
An explicit `fail-import` ends its rank immediately; the coordinator submits no
further invocations, including same-rank subscribers, after accepting that stop.
Running inputs remain pinned against collection, including shared raw digests.
Production passes `request.workers` to both archived-content and final-content
passes; CLI `process` retains the saved request's worker count (or the ordinary
default when no request is saved). Source admission retains its existing
serialized publication path.
Progress uses logical pool lanes; finishing one lane preserves other active jobs.
Coordinator source-style publication events retain counters without replacing
worker phases while any processing lane is active, including when lane 1 is idle.

Writer connections add `jobs_status_order(status,job_id)` to existing V2 queues.
Dispatch filters pending jobs through that index, using correlated primary-key
parent checks; per-message dispatch exposes a direct message-ID predicate to the
existing message index. Pending-ingest recovery uses the same parent check.
No completed history or dependency state is discarded.

Optional dispatcher callbacks report job preparation, processor invocation and
finished attempts (including failures/aborts). The production resume wrappers
forward these events to the status driver and clear active state in `finally`.
`processing_jobs_completed` counts finished attempts in this run, independently
of imported messages; current processor/job and active-worker fields update
through the existing worker queue. Derived jobs can emit more jobs, so their
total and ETA remain unknown rather than inventing a source percentage.

The source worker releases each message's cached raw bytes after MBOX publication
and catalog/processing commits, under the publication lock and writer lease.
Dispatcher completion also releases unused objects after job commits. Ingest
rank transitions defer this release until the outer source observation commits.
Operation shutdown sweeps orphaned SHA-256 files and owned interrupted staging
files. Unknown files and symlinks are preserved.

An additive `object_references` index records job inputs and successful checkpoint
outputs in their existing transactions. Existing V2 archives backfill the index
atomically on first writer connection; canonical formats and schema versions are
unchanged. Per-message collection uses indexed job/message identities and digest
references, avoiding a full job-history or objects-directory scan per download.
Live derived inputs and checkpoint outputs remain available across retries.

Raw copies may be released even with deferred jobs: production dispatch restores
them from SHA-256-verified canonical MBOX locations. Unpublished or damaged roots
remain. Harnesses retain original snapshots because they lack a canonical copy.
Historical terminal-job paths are audit identities rather than resident-file
promises. Collection is restartable; interrupted deletion leaves extra files.
`make test-processing-cleanup` and `make test-imap` cover canonical reconstruction,
deferred/failed work, reference-index backfill, and release during a live import.

`ClamAVProcessor` reuses saved host evidence only for `clean` and `infected`.
Other saved statuses call the current scanner session; previous failed invocations
remain in the processing audit history. Resumption needs no source reread or
source access, since retained raw objects are already digest-verified.
Current incomplete/failed scans permit filing with their actual provenance and
an antivirus metadata defect. `SourceMetadata.scan_session` permits reuse of a
fresh source-worker result only in its originating session; old unsuccessful
evidence retries through the current engine. Host `failed` handling accepts
scanner invocation exceptions/deadlines without changing their failed audit
record, and permits the subsequent filing rank to preserve the original.
An additive `accepted_invocations` table saves the host decision in the same
transaction as the failed invocation. Recovery replays that decision as a
checkpoint rather than rescanning after canonical publication; old failures
without an accepted decision still retry normally.
Other processor failures retain their existing dependency barriers.
After scanner startup, progress switches to `preparing processing`, then
`recovering archived content` and `recovering pending imports`. With ingestion
and unlimited content work enabled, the host drains eligible message/content
jobs before replaying unpublished ingest roots or traversing sources, then runs
content processing again for newly admitted mail. Deferred or explicitly bounded
content runs retain their previous dispatch order and job budget.
The preliminary pass retains failed jobs without raising their aggregate error;
the ordinary final content pass still reports failures after mail admission.
Its dispatcher continues independent jobs after failure while leaving descendants
blocked behind failed prerequisites. Explicit `fail-import` results still stop the
run, and cancellation still propagates. Normal dispatch retains its stop-on-error
behavior; the preliminary pass is the only production caller opting into continuation.
Failure summaries use the failed job's own invocation or input error, avoiding
misattribution to an unrelated older extraction/scanner failure.
`make test-ingest-recovery` uses real clean/EICAR scans, retained typed failures,
repeat CLI recovery, a damaged pending object and independent atomic index repair.

Current implementation and validation boundaries. Deferred feature designs
live in [plans](../plans/README.md); historical evidence does not validate this revision.

### CLI processor framework

Dispatch job identity, provenance and cancellation follow the [processor requirements](../requirements/PROCESSING.md#production-cli-processor-and-identity-integration).


For persistent attempt counts, timing and error reporting, follow the [processor contract](../PLUGINS.md).


For About inventory discovery and compatibility, follow [PLUGINS.md](../PLUGINS.md).


Keep header and signature counts separate as defined in [PLUGINS.md](../PLUGINS.md).


Persist scanner evidence and retryable failures according to [PLUGINS.md](../PLUGINS.md).


Promote attached messages only under the transfer-validation rules in [PLUGINS.md](../PLUGINS.md).


Apply the configured MIME, attached-message and text limits in [PLUGINS.md](../PLUGINS.md); retain failed, retryable jobs.

`MimeLimits` and a traversal-local `MimeBudget` charge all split/decoded output
before writing. A root MIME traversal visits nested attached messages before
releasing any child emissions, so budgets cover the complete tree without
per-message resets bypassing the root limit. Part and attached-message counts
are checked before creating/decoding further children. Limit exceptions make
the job retryable; per-invocation temporary files are cleaned. Quoted-printable
decoding preserves incomplete escapes across bounded chunks. `make test-mime-limits`
uses actual CLI archives to check failure, preservation and configuration retry.

The processing package implements API v2 manifests, typed objects/results,
in-process execution and a serial rank-barrier dispatcher. Make processor
provides init, plugins, submit, run, status and explicit reprocess commands.
Each archive is protected by the existing writer lease. Queue release and job
checkpoints use SQLite transactions; emission files are copied and fsynced
before committing references. Parent-job dependencies prevent work from
overtaking failed ranks. Restart recovers abandoned running work and reuses
completed invocations.

processing/sql/V2__processing.sql is a fresh framework schema, not a V1
migration. It is packaged independently of the production sql/ directory.
The focused framework target also runs catalog regression tests to enforce
that separation.
It includes the identity and organization relationships needed by the pickers.
Production CLI ingest now calls `ProductionPipeline` from the existing source
integrity/publication host. The three registered trees scan/file, extract headers
and queue content, then dispatch streamed MIME payloads to text, conversion,
identity and attached-message plugins. Canonical publication keeps its existing
journal and deduplication key. `processing.sqlite3` records raw references,
occurrences, jobs, evidence and picker state beside the catalog/search databases.
`process` resumes saved jobs without rereading sources; incomplete source
traversals are continued by repeating ingest. Configuration/code changes and
explicit replay rebuild automatic evidence while retaining manual decisions.
The default registry is packaged under `plugins/processors`; `processors` lists
it. The independent `make processor` fixture harness remains available.

Python plugins run synchronously in the host. A per-call context binds namespace
settings, deadline and cancellation checks. `remaining_seconds` bounds native
scanner I/O; POSIX main-thread invocations also receive an alarm. Portable or
background-thread execution requires cooperative checks. Late results never
publish. There is no separate Python worker executable. The Rust PST adapter
alone uses the external importer protocol, spooling output and retaining failed
tails with source/executable hashes and diagnostics under `processing-pst`.
`make test` builds the helper; real fixture tests cover partial-run recovery.

`make test-processors` runs lint/types and substantive framework tests;
`make test-cli-processors` validates production pipelines, deferred resume,
HTML/RTF selection, child byte preservation, identity edits and PST extraction.
No native windows or private archives are used. Desktop processor integration is
validated separately by `make test-gui-processing`.

On a positive ClamAV result, best-effort header metadata cannot prevent filing.
If parsing fails, quarantine records the raw digest as identity, labels its
required date/envelope placeholder `quarantine-unknown`, records the defect and
aborts subsequent processors. A real undated EICAR CLI test verifies exact bytes,
the positive scan checkpoint and exclusion from search/content processing.

HTML, RTF, text indexing and signature extraction use bounded reads and an 8 MiB
default UTF-8 output bound (`max_text_bytes` in each plugin's configuration).
Over-limit parts abort with a retained-content diagnostic. Generation invalidation
attaches the search database to the processing connection and uses SQLite's
rollback-journal multi-database transaction to remove FTS/suggestion data and queue
replacement jobs together; a SQLite trigger that rejects deletion checks rollback
of both databases after search-row changes have begun.
Provider domains use a built-in local set plus `plugins.identity-evidence.provider_domains`;
their evidence remains, but automatic affiliations are suppressed. PST receipts
record exit status after reaping even on timeout. Retained stdout/stderr prefixes
obey configured limits, with observed sizes and truncation flags in the receipt.

ProcessingObject.get_my_config() returns a defensive dictionary snapshot from
plugins.<manifest kind> in installation and archive config.yaml, with archive
values taking precedence. write_my_config(values, scope="archive") stages a
replacement of that plugin's selected layer; scope="installation" selects the
shared layer. Installation config.yaml lives beside application preferences;
the CLI --installation-config option provides an explicit path. The parent
preflights all writes after a successful rank using file locks and expected-value
hashes, then journals the batch before atomic file replacements. Recovery finishes
the batch before plugin reads. Equal-value replay is harmless; preflight conflicts
fail the job and require fresh invocations. See PLUGINS.md for the API.
Tests cover recursive precedence, namespace isolation, both write scopes,
abort/exception/timeout isolation and malformed settings without mocks.
Regression tests also cover later-plugin conflicts, interrupted multi-file
configuration publication, unreadable input recovery, invalid MIME tokens and
7z inventory size/digest checks.

The verifier SIGINT tests synchronize on consumption of FIFO data before
sending the signal, then close the writer so buffered I/O returns and Python
can deliver a pending signal. Holding the writer open indefinitely previously
caused intermittent CI timeouts. The tests require exit 130 with an incomplete
verification message, no traceback and no success output in normal/quiet modes;
they do not assert interrupt latency for I/O that never returns.

## Desktop processing and synthetic matcher

### Registered processor graph

`processor_graph.py` builds typed MIME/processor nodes and edges from manifests.
The graph and registry cycle validation share `registry.emitted_types`, including
registered output-only types in MIME-part expansion. Arbitrary additional MIME
types terminate at **Other MIME types**; pipeline-specific panels preserve the
dispatcher's acyclic type semantics. The view depicts subscriptions/emissions;
rank dependencies, persistence and stage handoffs remain in processor details
and the architecture documentation.

About's graph command opens/focuses one native window listed in the Window menu.
Its narrow read-only bridge reads the current About registry with the same
bounded plugin-settings lock wait. Refresh retains previous graphs on errors
and discards obsolete replies. Vendored Dagre 3.1.1 (including Graphlib) lays out
the DAGs locally; SVG and a text connection list use safe DOM text APIs for
custom plugin names. Fit/zoom controls and scrollable panels support large graphs.
No CDN, graph server, plugin execution or message access is involved.
`make test-processor-graph` exercises real built-in/custom registries, DAG edges,
offline browser rendering, safe custom labels, busy retry, and archive switching.
`make test-native-application` opens the graph from About through the native bridge.

### Processing services

[The proposed processor graphs](../PLUGINS.md#processing-graph-design-and-deferred-capabilities)
and website plugin page describe the agreed direction, including resumable work
selection, handoff plugins, first-class attached messages and durable tags.
Ingest owns scanning and filing, message processing owns header metadata, and
content processing owns MIME dispatch. Content-only resumption operates on
eligible already-ingested messages. Standard deduplication shares message
content/metadata while separate observations retain attachment provenance.
All plugins may access all headers and content of their current message.
Filing may read what it needs after ClamAV. Content-discovered child messages
enter message processing directly without another antivirus scan, retaining
parent scan provenance; the handoff uses shared deduplication/publication
services to establish their records and durable content references.
The graphic is a shared SVG in `website/static/images/processor-dag.svg`.
The CLI dispatcher, production extraction, attachment promotion and identity/tag
persistence are implemented. `gui_processing.py` supplies read-only status and
identity queries plus writer-leased manual edits. `GuiApi.resume_processing`
builds an `IngestRequest` from saved policy and starts the existing GUI import
thread; it never starts a Python plugin subprocess. Ingest saves resolved owner
rules and plugin/config paths in `processing_settings` before message processing.
Run history preserves unfinished source roots across later rootless content runs.
Content-only work reuses archived objects, and read-only access never creates a
missing processing database. Unsupported/missing saved policy reports an error.

`processing.js` offers **Continue Import** for both phases, **Leave import suspended**,
and **Stop import**, without phase checkboxes. It provides an explicit
**Continue Processing** action to reopen the dialog. The action starts disabled until status
is known, stays disabled during the resume bridge request, and follows the shared
ingest-overview poll afterward. The overview includes the document's accepted
job before a status file exists. Resume generations reject replies started before
a transition, while monotonically applied request numbers reject reordered polls
without starving responses slower than the polling interval.
Completion or rejection refreshes its state in every window.
`identity.html` and `identity.js` subclass the shared
matcher widgets with database queries, distinct per-group counts and immediate
manual writes. Name moves and separation update manual address assignments;
institutions retain domain-based membership and permit name edits. Prototype
undo/reset are not exposed for persisted edits. Authoritative matching remains
unconnected and disabled. About groups the registered processor manifests by
subscribed type. `gui_provenance.py` reads attachment tags and parent occurrences;
search rows show the gray tag and the viewer links to each parent and MIME path.
A parent outside the current search results opens in the viewer without trying
to select or scroll to an absent result row.
`make website-preview-screenshots` builds the website and renders its homepage,
Searching and Importing pages using local assets into ignored checkout-root output.
The headless tests use real bridges, queues and SQLite databases, including edits
surviving replay, competing writers, content resume without source files, and
attached-message navigation. Native macOS window behavior remains an opt-in check.

The standalone `make matcher-prototype` opens independent name and institution
windows through a temporary `LoopbackAssetServer` and pywebview. Use
`ARGS="--kind name"` or `ARGS="--kind institution"` to open just one. Closing both
windows stops the server. The launcher does not construct the archive
application, read preferences, or open databases. `gui/matcher.js` defines the
shared `MatcherWindow`, model, and immutable message/address record classes.
`matcher-types.js` supplies `NameMatcherWindow` and `InstitutionMatcherWindow`
subclasses with their respective labels and matcher callbacks.
`matcher-demo.js` supplies twelve fictional addresses, deterministic synthetic
message dates/identifiers, name groups, and institution groups (including an
explicit unassigned/personal group). Its two predefined name matches and one
institution match do not execute an authoritative research algorithm.

The shared window owns 39px disclosure triangles, mailbox/domain substring
filters, optional inclusive date bounds, header sorting, selection, parent/child
drag moves, a destination picker including hidden groups, separation, reset, and
session-only undo. Date filters project message observations without changing
the source records; first/last use and counts reflect that projection. Empty
date controls display “Any date”, overriding WKWebView's current-date placeholder
while leaving the native date editor available on focus. Group
counts use distinct message identifiers, including a shared-message fixture.
Clicking Messages initially sorts descending; other headers initially sort
ascending. Repeated clicks reverse direction, with `aria-sort` and visible
arrows. Sorting reorders groups and their children independently.
Parent moves include hidden children and every original observation; the
selection toolbar reports the complete source count. A matcher batch applies
to a separate model and becomes one undo step; failures leave data unchanged,
and mutation controls are disabled while it runs. Filter edits reveal matching
children; disclosure can then collapse them. A filtered group badge shows
matching/total addresses. `make test-matcher-prototype` runs Ruff, focused
lint/type checks, and real Chromium interactions for filters, inclusive/open
and reversed date ranges, count deduplication, sorting, disclosure, dragging,
separation, undo/reset, matcher batches, both subclasses, and minimum layout.
This is a synthetic UI experiment; catalog integration and authoritative
matching remain unimplemented. Browser evidence does not establish native Cocoa
drag behavior; manual testing uses `make matcher-prototype`.

## Catalog-derived Contacts

Contacts are a derived address-level projection over the catalog. The projection uses each message's `From`, `To`, `Cc`, and `Bcc` headers once and write
one deduplicated address appearance per message. It derives the
default **Meaningful** relation: outgoing direct To/Bcc recipients and incoming
From addresses only when an exact configured owner address is in To. This
preserves all-header statistics while avoiding Cc and indirect mailing-list
traffic in meaningful-contact results.
The include/exclude owner rules route Sent mail. A separate owner-address
editor remains planned; see [plans/CONTACTS.md](../plans/CONTACTS.md).
`mailarchiver human-contacts` is the initial read-only consumer. Its
`--owner-address-file` accepts the existing owner aliases with newline, comma,
or semicolon separators; it resolves them only to catalogued Sent sender
addresses. Optional repeatable `--owner-address` values are exact overrides.
The command defaults to meaningful Contacts and exposes the all-header
projection only with `--all`; table, TSV, and JSON output share one typed row
model.
Contacts opens the catalog with `Path.resolve().as_uri()` and SQLite `mode=ro`,
matching the schema validators. This preserves Windows drive-letter URI syntax
and encodes path characters without allowing database creation or writes.
Before rendering, it applies the strict versioned `contact_filters.yaml`
policy. It classifies malformed Unicode/control values and forbidden local-part
characters as bogus, configured provider/list patterns as mailing lists,
configured automated patterns as service identities, and only then includes
the remaining addresses. The policy deliberately keeps ordinary SMTPUTF8
addresses possible; it does not use non-ASCII alone as a rejection rule.
Bogus local-part and domain patterns report `invalid-local-part` and
`invalid-domain` respectively, with local-part rules evaluated first. Empty
domains are also rejected as `invalid-domain` after the local-part checks.
The policy resolver first checks `<archive>/contact_filters.yaml`; when
that file is absent it uses `src/mailarchiver/contact_filters.yaml` from
the installed package. A `mode: replace` archive policy is a complete strict
Pydantic replacement. A `mode: extend` policy may provide rule lists only; it
is unioned with the packaged lists in order, removing duplicates, while
packaged scalar values remain authoritative. The effective policy is therefore
complete and reproducible without field-by-field scalar merging.
Each pattern uses a Pydantic `AfterValidator` to compile with the same
case-insensitive flags used by classification. Regex and YAML errors become
path-qualified `ValueError` diagnostics handled by the Contacts CLI; invalid
rules cannot remain hidden behind earlier matches or an empty catalog.

## Signature phone display

The production identity processor also emits typed `PhoneNumber` candidates from
its detected signature text. `phones.py` retains optional `+` and 7–15 digits as
the comparison key, keeps the observed display form, excludes date-shaped tokens,
and trims the complete text at reply boundaries before selecting a signature or
last-line window. The host stores these as generic `phone` evidence
on the sender address without inserting email-address observations. Identity
queries deduplicate numbers within the visible date range. Processor version 2
collects these observations during content processing; previously processed
archives need explicit reprocessing to acquire them. Merely opening the picker
does not reprocess messages or import private research evidence.

## Optional name-linkage comparison

An additional Splink comparison enables native full-name term-frequency
adjustments. The unadjusted engine remains a separate baseline. A real-engine
regression compares rare/common exact names while holding other fields equal.
Existing frozen reports retain their original scores until a separate run;
review rows also show frequencies over the retained name variants.

## SBook entity filter

`name_entities.py` consumes pinned, licensed SBook5 company/address Flex sources,
company expressions and first-name data under `mailarchiver/sbook`. It translates
only whole-word company/organization rules and literal quote spans, not general
scanner order, stock/city/address inference or every legacy pattern. Unicode
boundaries and person/company conflicts retain uncertain names as unknown.
Service mailbox prefixes are classified separately. This is an offline heuristic,
not measured entity-classification accuracy. Packaged data needs no runtime Flex.

`IdentityFilter.include_non_people` restores excluded addresses in the Name picker;
institution projections retain them. Counts and search selections use the visible
members. The private report filters its original rows without renumbering them;
automatic classifications do not become owner labels. Show non-person identities
also reveals owner-flagged rows. Export always retains all fifty decisions.

## Research comparison implementation

`evaluate.py` validates exported owner pair IDs and address mappings before
joining frozen scores. It reports direct-pair confusion counts at fixed cutoffs,
separately including all identity judgments and excluding explicit owner
non-person annotations. Legacy `not-person` labels and notes containing that
explicit phrase are exclusions, not guessed link decisions. A same-organization
note alone is not promoted to a positive. These selected-sample diagnostics do
not estimate archive recall/precision or train any model. Report files are new
private derivatives; input labels, experiments and archives stay read-only.

`scripts/name_matcher/linkage.py` defines bounded typed address profiles, frozen
candidate blocks, fixed rules, an unlabeled binary-feature EM mixture and
synthetic cluster/pair metrics. `engines.py` adapts real Dedupe and Splink APIs
and performs two rounds of equivalence-projected correspondent inference.
`experiments.py` aggregates verified header/signature evidence and catalog
correspondents into a separate SQLite database, times engines, retains complete
scores and emits a private HTML owner-review report. Optional packages are
locked under `research/name-matcher`; normal application dependencies are unchanged.
The report stores and exports a boolean `not_person` independently of its
`match`/`non-match`/`insufficient` label; notes identify whether A, B or both are
non-person identities. Legacy `not-person` labels become checked flags with an
unlabeled link decision. Stable pair/storage keys preserve decisions and
unlabeled comments across report UI updates.
Dedupe currently trains on synthetic labels; Splink fits random-pair u values
and blocked EM without owner labels. The repetition ablation measures recurring
already-detected signature blocks, not the unimplemented general repeated-tail
extractor. Fifty representative proposals are intentionally selected and cannot
supply unbiased real-archive accuracy. `make test-name-matcher-experiments` runs
ordered lint/type checks and real fixture/engine tests.
