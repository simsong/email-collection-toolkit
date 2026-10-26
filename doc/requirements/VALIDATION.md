<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Validation requirements

Current subsystem contract and acceptance boundaries. This is not a claim of
platform/release acceptance. Deferred features live in [plans](../plans/README.md).

## Shared acceptance coverage

Native desktop acceptance must retain a phase report and diagnostics after a
parent timeout, for source and relocated package runs. Both use the same overall
deadline, which must permit the existing import deadline and
Preferences Close/reopen checks without dropping their assertions. CI must allow
the complete ordinary suite to finish; timeout changes do not establish success.

Reader acceptance must retain search, Unicode rendering, exact export and source
immutability under URI-reserved archive paths. Shared scanner checks must cover
direct and registered APIs, raw and MIME EICAR, concurrency and startup cleanup on
both platforms. The Windows scanner target must include the shared deadline case.
Legacy MBOX parameterization must retain ambiguous quoting, missing final newline
and source-envelope cases; similar setup does not make their inputs redundant.

## Offline IMAP fixtures

Disposable IMAP server greetings shall use a fixed local identity rather than
resolving the runner hostname. Integration tests shall retain real TLS validation,
read-only source checks, byte normalization and interrupt/restart behavior.
Fixture setup connections shall have bounded timeouts so failures cannot hang
indefinitely. No change to the production downloader's socket timeout is required.
Pytest shall own server startup/teardown and seed three INBOX messages. Both the
CLI and GUI background entry point must archive them, finish indexing searchable
bodies/attachments, and rerun without duplicate publication or source flag changes.
Retain distinct protocol, TLS-rejection, byte-normalization and interruption tests;
a successful three-message import does not establish those failure boundaries.

## Recovered offline diagnostic boundaries

ClamAV initialization and native scanning must have hard deadlines and remove
temporary plaintext even on timeout. Known incomplete EMLX records must be
reported without blocking complete messages in a directory import, while direct
selection fails explicitly.

Private h3 review exports and name-evidence databases must be new derivatives
outside source stores. Canonical message bytes are verified before extraction;
neither tool changes an archive, chooses deduplication by h3 alone, or sends
message evidence to a remote model. Edited review manifests must not redirect
reads or writes through escaping paths or symbolic links.
H3 review publication must reserve a new destination exclusively, refusing even
an empty directory created during extraction, and publish its completion manifest
last. Deferred AI correlation must reject duplicate request IDs, including
identical requests that produce the same deterministic ID.
Name-evidence publication must not replace an output created concurrently.
Both database and summary must be staged before publication; failure to publish
the summary must remove the database link created by that attempt, preserving
any competing output. The h3 comparison index must attach the source catalog
explicitly read-only with SQLite URI handling enabled.
The current prototype requires hard-link support and owner-only permissions on
its output filesystem; it must not substitute an overwriting rename.

## Independent Rust import verification

`make verify-database ARCHIVE=...` shall read existing catalog/search databases
without SQL writes and independently verify every catalogued MBOX location.
For a quiescent, checkpointed schema-v1 archive, require SQLite integrity and
foreign-key consistency; complete message/location coverage; whole-MBOX byte
counts and SHA-256; nonoverlapping, gap-free records; and raw message SHA-256
recovery using mboxrd, bounded legacy mboxo, adopted envelopes and final-newline
candidates. Reject pending publications, unfinished runs, unsafe mailbox paths,
unregistered MBOX files, inconsistent publication observations, missing/extra
search digests, quarantine leakage, and broken FTS/attachment metadata mappings.
Reject WAL headers and database journals/sidecars before SQLite opens them,
without creating shared-memory files. Stream mailbox and message bytes with
bounded buffers, including arbitrarily long adopted envelope lines, while staying
within each catalogued record. Mixed legacy interpretations must share a streamed
pass instead of rereading the record for every quote mask; retain the twelve-line
ambiguity bound and bounded candidate hash memory. Fail rather than repair.

This first Rust verifier does not recompute semantic hashes, parsed metadata,
extracted search text, or processing/manual-decision data; it does not establish
source completeness. The separate installed Python verifier continues to check
BagIt/Mailbag and semantic fixity. Stop all writers before either verifier runs;
a read transaction does not synchronize independent databases and MBOX files.

Rust owns the complete-corpus and synthetic import/database end-to-end assertions.
Tests invoke the real Python importer and installed portable verifier as child
programs, use the real embedded scanner, compare reviewed source/hash expectations,
check source preservation and repeat-import idempotence, and reject deliberate
catalog/MBOX/search corruption. A subprocess deadline must terminate test-owned
children. Golden updates remain an explicit, reviewed maintenance action with
private expectations in ignored output. Browser/native test migration is deferred.
`make test-import-e2e` is required by `make test-e2e` and therefore `make check`;
plain Cargo tests report these external-prerequisite tests as ignored.

## Public validation datasets

* Validation definitions are strict, versioned TOML files, one per anonymously
  downloadable public corpus. Each records its source URL, preprocessing mode,
  extraction bound, and EC2 sizing. Account-gated, institution-only, and
  unavailable bulk exports are excluded. Fixed cross-era samples are identified
  as samples rather than represented as complete list archives.
* All validation work is derived under the repository-local ignored `data/`
  directory. Downloads are immutable inputs: acquisition records their byte
  length and SHA-256 in a source manifest, reuses only a hash-checked cached
  file when an expected digest is configured, and never changes an upstream
  mailbox or downloaded artifact.
* Tar, ZIP, gzip, and 7z preprocessing rejects path traversal, links and special
  archive members, and expansion beyond the dataset's configured bound. Normal
  RFC 5322 and MBOX inputs are hard-linked or copied byte-for-byte. A Unix MBOX
  envelope on an individual-message corpus is removed with the MBOX parser;
  SF-LOVERS Babyl records are likewise an explicitly derived conversion. The
  original downloaded files and their source manifest remain unchanged.
* A local validation run acquires and preprocesses a dataset, invokes the normal
  ingest CLI with on-demand ClamAV, runs the installed standard-library verifier,
  and creates a ZIP of the verified Mailbag plus a typed run report. A run over
  all datasets is sequential so local resource use stays bounded.
* The AWS mode deploys a SAM control plane without creating the result bucket.
  The existing long-lived S3 bucket is a deployment parameter. Starting all
  datasets launches exactly one independent EC2 worker per dataset. Workers have
  encrypted delete-on-termination storage, required IMDSv2, no inbound security
  group rules, and write-only access beneath the configured bucket prefix.
  Each worker downloads its own corpus, runs the same Makefile workflow, uploads
  status and logs plus any report and Mailbag ZIP, and terminates on shutdown,
  including after failure.

## Developer validation gates

Before pr-to-ready changes begin, inspect open PRs, active tasks, checkouts, and
unpublished work for potential file, behavior, dependency, or shared-resource
conflicts. List each affected task/PR, branch, path, overlap, and proposed
coordination plan; obtain explicit user approval before conflicting work.
Recheck before integration/publication and when scope changes; an existing
approval covers only the disclosed conflict and plan.

Copilot review requests must use `gh` with the authorized `simsong` identity,
not browser control. That exception is review-request-only; all other Codex
GitHub writes retain `simsong-agent`. A successful command alone does not prove
that review was requested.

Before pr-to-ready handoff, intended task changes and local-only commits in
every task checkout must be reconciled, validated, and published in the delivery
PR. Moving or backing up a dirty checkout is not integration. Unrelated or
ambiguous work requires an explicit disposition rather than silent inclusion
or deletion.

The pr-to-ready workflow must retain a quiet post-handoff merge check and clean
up its task-owned linked checkout only after the human merges the PR. Removal
requires current-main ancestry or patch-equivalence evidence and a clean tree;
ignored private evidence is not disposable. Dirty, unmerged, or uncertain
checkouts must be retained and reported, not forcibly deleted.
When the user explicitly authorizes earlier removal of clean, pushed checkouts,
verify remote reachability of all local commits and the matching open PR head,
preserve non-rebuildable artifacts, and confirm no active task needs the path.
Keep unmerged branch refs until merge verification. Every handoff must report
removed and retained task checkouts, artifact locations, and remaining blockers;
remove retired checkout entries from the skill distribution inventory.

`make check` runs Ruff and Pylint (`make lint`), then ty and Pyright
(`make types`), then pytest, Chromium end-to-end tests, and website validation.
The stages run sequentially even with parallel make and stop on failure.
Both type checkers cover source, scripts, tests, end-to-end tests, and the AWS
launcher. Project development dependencies and type stubs are locked with uv;
static analysis must produce no errors or warnings. Focused `make ruff`,
`make pylint`, `make ty`, `make pyright`, and `make test` targets remain available.

### Desktop review follow-up

The portability audit reads each Mach-O LC_RPATH command, expands loader and
executable-relative paths, rejects search paths outside the bundle, and requires
non-system dependencies to resolve to bundled files. Missing antivirus uses a
platform-neutral confirmation on non-macOS hosts. Source-picker navigation is
saved only after successful ingest while the writer lease is retained; a failed
import leaves the previous directory unchanged.

### Writer and desktop review boundary

Windows writing must preserve the same bytes and recovery semantics as POSIX.
Native directory handles must reject reparse points and pin archive paths;
single-link lock files and OS-owned shared/exclusive locks must protect writers
and application installation. Windows MBOX input/output must not translate
line endings according to the host OS. Scanner and native GUI acceptance remain
required independently of storage tests.
POSIX acquisition pins the archive/status directories and opens lock files
relative to directory descriptors without following links. Lock files must be
regular, single-link files. New targets are created under a parent-directory
creation lock before acquiring the archive lease; creation diagnostics may leave
`.mailarchiver-create.lock` in the parent. Its presence alone does not lock anything.
GUI creation rechecks destination emptiness under the lease. File New proceeds
into Import, About reports the active archive volume, and publication refreshes
mailbox-only queries as well as text queries.

### PR review validation follow-up

Archive documents identify directories by filesystem device/inode and reject
symbolic or hard-linked database entries before opening. GUI ingest records
publication evidence even when a later step fails; only published changes
advance the shared generation. Progress can write status files without a
terminal stream, including windowed builds with no stderr. Ingest child windows
route document actions to an attached search window. Informational notices stay
in About instead of appearing as errors. Closing an import owner offers waiting
or keeping the window open. Native macOS Quit offers Cancel or Stop Import and Quit while an import is
active. Confirmed Quit signals all imports at message boundaries and allows at
most five seconds before forced exit; idle Quit exits immediately. Workers retain
leases until checkpoint completion or process death. Makefile Ruff checks select this checkout's configuration explicitly. Git
selects tracked and non-ignored new `.py` and `.pyi` files, so linked worktrees
are checked without descending into ignored generated directories.

An Ingests child window retains document routing after all search windows close.
New Search and Ingests use that document directly; Import creates a new search
owner when needed.

Integrity hash-standard versions must be JSON integers. The standalone verifier
rejects boolean, floating-point, and string alternatives without coercion.

## Current acquisition boundaries

The application contains no OAuth client or account authorization; see
[Remote account authorization](ACQUISITION.md#remote-account-authorization).

Directory import reports and skips `.partial.emlx` records while retaining
complete supported records. Direct selection of a partial record is rejected,
including zero-byte partial records; the generic empty-file shortcut does not
silence them.
This is not a complete mailbox acquisition: detached attachment bytes are not
reconstructed. Do not modify the source cache; export mail through Apple Mail
when a complete MBOX source is required.

## Experimental ePADD address-book export

`make addressbook-export` reads only sender/recipient addresses referenced by
messages in the selected archive catalog and writes a private text derivative
outside the archive. The first contact contains explicitly selected exact owner
addresses or, with `--owners-from-sent`, distinct sender addresses from catalog
messages classified Sent. Recipients are never treated as owners merely because
they received Sent mail. Historical Sent classification can itself contain false
positives; it is not independently verified owner identity. All other addresses
remain separate, with no inferred display-name aliases. Single-entry contacts
preserve identifiers without `@` using the ePADD 11.1.3 reader's fallback; Sent
identifiers without `@` remain separate and are reported because they cannot be
represented as owner email aliases in this format. Unsafe contact lines are
excluded and individually reported; case normalization, totals, and SHA-256
evidence are recorded. Output must not replace existing files or mutate the
source. This is a whole-address-book replacement experiment, not a reviewed
People authority or validated live repair.

## Documentation ownership

[README.md](../README.md) indexes current guides, plans and historical records.
[CLI_REFERENCE.md](../CLI_REFERENCE.md) owns executable command syntax and options;
user workflows link there instead of maintaining separate option inventories.
[RUST_GUI_EXPERIMENTS.md](../RUST_GUI_EXPERIMENTS.md) owns retired desktop experiments.
Specialist guides own their byte-level format, provider and platform contracts.
Requirements state invariants; implementation maps them to code and evidence.
Historical logs are dated and cannot override current behavior.
