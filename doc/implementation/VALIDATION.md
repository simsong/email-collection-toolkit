<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Validation implementation

Current implementation and validation boundaries. Deferred feature designs
live in [plans](../plans/README.md); historical evidence does not validate this revision.

## Shared acceptance coverage

`test_reserved_characters_and_read_only_attachments` checks SQLite URI handling;
`test_reader_archive_under_uri_reserved_path` runs the complete shared reader
acceptance (all sort orders, Unicode rendering, exact export and unchanged files),
replacing the duplicate ordinary-path invocation. Both are included by
`make test-python-reader`.
The concurrent real-scanner test shares one engine across registered and direct
calls, retaining plain clean/EICAR and MIME cases. The startup-deadline test checks
both worker and connection cleanup and is reused by `make test-windows-scanner`;
Windows DLL/SSL and inherited-owner regressions remain platform-specific.
The existing routing/import regression also checks absent legacy daemon settings
and unchanged sources, avoiding a separate successful import for the same contract.
Three legacy MBOX recovery inputs share one parameterized test with named cases.

## Offline IMAP fixtures

The autouse fixture defined in the module in `tests/test_imap.py` sets pymap's `FQDN`
configuration to `localhost` for each test and restores the original environment
afterward. This bypasses pymap's synchronous `socket.getfqdn()` greeting fallback,
which can block its asyncio server loop on hosted-runner DNS. Fixture setup
connections use a ten-second timeout. `make test-imap` checks the greeting through
real TLS connections, including a subprocess whose audit hook rejects reverse-DNS
lookups, and retains all acquisition and recovery assertions.
Function-scoped pytest fixtures own each listener, seed three messages (attachments,
duplicate Message-ID with different content, malformed MIME/encoding), and configure
an isolated archive/keyring. The existing TLS-mode CLI cases now assert production
body/attachment search and completed processing jobs. A separate headless GUI test
runs `start_import` on its actual background thread and repeats the import, checking
the GUI search bridge, canonical hashes/integrity, UID checkpoints and unchanged
seen/unseen flags. These run in ordinary pytest/`make check`; native window behavior
and live Gmail remain separate acceptance boundaries. Shared fixtures replace
repeated listener setup/teardown, without dropping distinct failure-mode tests.

## CI and release validation

The ordinary Python/browser job allows 60 minutes. Pytest and the focused Windows
writer target report their 20 slowest cases. The native desktop child allows 300
seconds, including its existing 120-second import check; it writes phase JSON and
45-second repeating stack dumps. On subprocess failure its parent prints retained
JSON/log tails. Windows CI retains synthetic native-reader logs, JSON and screenshot
under `dist/native-reader`, including failed runs. All reader, preservation,
Preferences and signing assertions remain required.

Ordinary runner jobs use macos-15. Continuous integration runs `make check-static`
and `make check-tests` in parallel jobs on non-`main` branch pushes that change
files outside `README.md` and `doc/RELEASE_NOTES.md`; local `make check` retains
their order. Other documentation still runs validation. Pages also skips
`main` pushes limited to those two files. CI has no PR/main duplicates.
Forked PRs are not covered by that push trigger. An explicit `[release-ci]`
head on any non-main branch runs signed/notarized DMG and Windows package gates
independently of the changed-file filter, without publishing. Temporary PFX
cleanup ownership is recorded before writing, so partial failed writes are
removed as well as complete files. Release publication requires a version-matching
annotated `v*` tag on `main` and invokes
`make dmg`, which mounts the candidate and runs its headless installed-app
self-test. Native GUI release checks remain an explicit local
`make check-release` action. Pages uses the same pinned Darwin Zola
binaries/checksums as application CI.

## Rust import verification and lifecycle tests

`rust/archive-verifier` independently checks imported archives. Its
`archive-verifier ARCHIVE` executable opens schema-v1 `archive.sqlite3` and
`search.sqlite3` read-only, checks SQLite/foreign-key integrity and publication
observations, streams MBOX files, and verifies each location against its raw
SHA-256. A streaming framing decoder tries reversible mboxrd, stored/fully decoded
legacy mboxo and mixed legacy interpretations for at most twelve ambiguous lines,
with adopted-envelope and writer-added LF/CRLF candidates. Up to six fast framing passes
are followed, when needed, by one shared mixed-legacy pass: incremental hash states
fork only at ambiguous quote decisions, with at most 8,192 states for twelve lines
and both envelope variants. Fixed two-byte tails keep per-state memory bounded;
shared prefixes are hashed before forking. Candidate hashing still costs work
proportional to the bounded state count, but record rereads never grow per mask.
A measured real-file regression checks late-mask/adopted recovery and corruption
within seven body passes. Catalogued offsets
must cover every record and separating LF; generation counts, sizes and whole-file
hashes must agree. Envelope lines are scanned within the catalogued record in
bounded chunks; the adopted-envelope SHA-256 state is fed incrementally, with no
line-length limit or whole-envelope allocation. Search checks compare normal-message
digest membership, FTS row links, quarantine exclusion and attachment metadata counts. Missing databases
are never created. The command reports counts only after every check passes.

This is consistency verification, not authenticity or a replacement for the
installed `verify_mail_archive.py`: semantic digests, parsed fields, full BagIt
metadata, extracted index text and `processing.sqlite3` are outside this first
component. Run against an idle archive; transactions cannot snapshot MBOX files
and both databases together. WAL headers and journal/sidecar files are rejected
before SQLite opens them, preventing read-only verification from creating shared-memory files. Unfinished
runs are rejected.

`make test-import-e2e` runs Rust-owned real-process acceptance tests, migrated
from `tests/test_corpus_import.py` and the lifecycle assertion in
`e2e_tests/test_ingest_verify.py`. The latter retains its Python fixture and
browser/native tests until their separate migration. `uv run` provides the
application interpreter; Rust launches `python -m mailarchiver` and the installed
portable verifier without importing Python test helpers. ClamAV is the existing
embedded engine, not a new service. Rust checks the reviewed corpus, reimport,
source fixity, status, synthetic EICAR routing, search and corruption failures.
Failures retain private artifacts below `.tmp/rust-import-tests/`; successful
runs delete them. Golden updates require `make update-corpus-expectations` and
still separate public tracked inputs from ignored private additions. Normal
Cargo tests exercise framing, diagnostics and timeout logic without real imports;
`make test-e2e` explicitly runs the external-prerequisite Rust tests before pytest
browser tests, so local and existing CI `make check-tests` gates include them.

## Current package shape

`dev/addressbook-exporter.py` implements the experimental ePADD 11.1.3 text
handoff through `make addressbook-export ARGS='--archive ... --output ...
--owners-from-sent'`; alternatively repeat `--owner` for exact aliases. Sent mode
uses the persisted sender classification, never recipient membership or new
display-name matching. Legacy owner addresses without `@` stay in singleton
blocks and are reported explicitly. It opens a journal-free
catalog with SQLite read-only/immutable mode, streams referenced addresses into
a temporary SQLite set, lowercases/deduplicates them, and emits the owner first,
then one address per contact. It adds no correspondent display names. UTF-8
output requires an ePADD JVM using UTF-8; legacy local/UUCP addresses use singleton
blocks. Blank, non-printing, delimiter-prefixed, HTML-entity-bearing, and overly long entries are
reported rather than emitted. The source catalog SHA-256 must remain unchanged.
Non-`@` identifiers requiring HTML escaping are also reported because ePADD's
fallback double-escapes them at contact boundaries.
Files are published without replacement at mode 0600, outside the source archive;
`OUTPUT.report.json` records exclusions, owner choices, counts, and hashes.
`make test-addressbook-export` checks real SQLite fixtures, source immutability,
owner grouping, parser hazards, and refusal paths after lint/type analysis.
The export does not retain ePADD curation, mailing-list flags, or inferred names;
upload into a copy before treating it as a repair. Older archive owner-token
files may reflect former substring semantics and are not an exact owner-address authority.

```text
email-collection-toolkit/
  pyproject.toml
  src/mailarchiver/
    __main__.py         CLI, ingest framework, worker/status coordinator
    plugin_api.py       versioned immutable plug-in contracts
    plugin_loader.py    trusted manifest discovery and frozen registries
    pdf_mail.py         standalone printed-email PDF extraction and derived MBOX
    plugins/            packaged source and physical-file manifests
    sources.py          local source plus MBOX/Babyl/EMLX/message generators
    source_stubs.py     explicit unavailable provider/stream source names
    source_integrity.py local source integrity controls
    archive_integrity.py Mailbag archive-integrity controls
    layout.py           native data/mbox and integrity tag paths
    bagit.py            Mailbag CSV and BagIt checkpoint publication
    mbox.py             mboxrd publication and verified location reads
    message.py          header/MIME/date/classification parsing
    catalog.py          packaged schema loading and database helpers
    sql/
      V1__archive.sql   authoritative archive.sqlite3 V1 schema
      V1__search.sql    disposable search.sqlite3 V1 schema
    search.py           disposable search.sqlite3 and FTS5 rebuild
    scanner.py          on-demand ClamAV lifecycle and scan client
    ingest_status.py    shared typed per-run JSON status contract and store
    standalone_verify.py installed source-independent verifier
  gui/                  pywebview HTML, CSS, and JavaScript assets
  scripts/data_quality/ read-only forensic audit and evidence tools
  tests/
```

The data-quality scripts reproduce the investigation that motivated the date,
Babyl, MBCP, and `From XXX` rules. Makefile targets require explicit archive
and source paths and write private derived evidence under ignored output in the
checkout root by default. The scripts open the archive catalog read-only, verify bytes retrieved
from canonical MBOX locations, leave all source and archive files unchanged,
and refuse to replace existing evidence files. The generated MBOX, CSV, and
JSON files are investigation artifacts, not repository fixtures.

## Public corpus validation pipeline

`mailarchiver-validation` loads strict Pydantic models from
`validation/datasets/*.toml`. The nine definitions cover Enron, SF-LOVERS,
SpamAssassin, bounded GNU emacs-devel, IETF-822, Apache httpd-dev, and GCC list
samples, a pinned lore.kernel.org public-inbox repository, and the historical
`comp.mail.mime` Usenet group. TREC07 and W3C mailbox exports are not configured
because their current official bulk routes are unavailable or authenticated;
the pipeline does not silently substitute account-gated mirrors.

Acquisition streams HTTP responses to temporary files and atomically installs
them after optional expected-digest validation. The source manifest records the
observed SHA-256 for every HTTP artifact and the resolved commit for Git. Safe
extractors reject traversal, links, special nodes, and oversized output. Normal
message/MBOX preparation uses hard links when possible and copies otherwise;
public-inbox Git blobs are streamed through one `git cat-file --batch` process.
Individual-message files with Unix MBOX envelope lines are parsed into derived
RFC 5322 files instead of being mistaken for multi-message mailboxes.
Babyl-to-RFC conversion is a derived SF-LOVERS preprocessing step, with every
downloaded source retained separately.

`make validation-run` invokes the ordinary ingest CLI and its private on-demand
ClamAV process, executes the verifier installed inside the resulting Mailbag,
then writes `data/results/<dataset>.mailbag.zip` and a hash-bearing JSON run
report. `make validation-run-all` performs this workflow sequentially. Downloads,
extractions, prepared inputs, Mailbags, reports, and ZIP files all remain beneath
the ignored repository-local `data/` tree.

`validation/template.yaml` is a SAM control plane, not an EC2 emulation. It
creates a Lambda launcher, egress-only worker security group, instance profile,
and least-privilege result-upload policy; the result bucket is an external stack
parameter. `make validation-aws-start-all` invokes the launcher once per enabled
configuration. Each invocation starts one Ubuntu EC2 instance with encrypted
delete-on-termination EBS, required IMDSv2, an on-demand ClamAV configuration,
and `InstanceInitiatedShutdownBehavior=terminate`. User data checks out the
configured public repository ref, runs the same Make target, uploads status and
logs plus any report and ZIP under a run-specific S3 prefix, and shuts down from
an EXIT trap. There is no SSH ingress or persistent worker fleet.

## Validation and tests

`scripts/check_copyright.py` enumerates Git-tracked files and checks the
explicit project-owned, comment-safe policy without rewriting anything.
`make copyright-check` runs it as part of `make check`. The exclusions protect
canonical mail and test fixtures, binary/data files, the generated release
index, the vendored Tabulator tree, and the website theme (which carries its
own project license file). Upstream CC-BY-SA artwork and generated shared-workflow
wrappers are also excluded; Python stubs and JavaScript modules receive native comments.
Missing notices produce a warning listing the affected paths and exit status 0,
so `make check` and source builds continue. Checker execution failures still
propagate normally. `make test-copyright` exercises the ownership boundaries and
runs the real checker in temporary Git repositories, verifying that a dependent
Make target runs with both missing and complete notices.

`scripts/check_runtime_licenses.py` starts with the installed `mailarchiver`
distribution and follows evaluated PEP 508 runtime requirements, rather than
inventorying the development environment wholesale. It records typed package,
version, license, and complete-license-file paths; rejects missing license
evidence and development-only packages in the runtime
closure; and can copy exact license files plus a JSON inventory into a binary
staging directory. This does not establish license compatibility.
`make runtime-license-check` is a CI and release gate.
`make runtime-license-bundle LICENSE_OUTPUT=PATH` is the packaging interface;
it creates a complete notices directory containing the project and third-party
notices, typed inventory, and collected license files. The current macOS builder
retains its existing notice collector; wiring the audited bundle into frozen
macOS and Windows builds remains pending. The audit runs
independently on each target platform because pywebview's closure is
platform-specific.

The native About metadata uses the same copyright notice. PEP 639
`license-files` metadata places the repository notices and complete licenses
for stored Tabulator and website-theme code in source distributions.
`make test-native-setup` exercises the current owner email include/exclude editor
before antivirus confirmation and verifies the saved rules after a synthetic
import. Setup reuses File Import's current revision-checked owner-rule workflow.

`make test-corpus-import` runs the single full-directory Rust regression in
`rust/archive-verifier/tests/imports.rs`, also required by `make test-import-e2e`
and `make check`. It imports the actual `tests/data` directory with `--clamav`, compares subjects/raw SHA-256
and per-source accounting against `tests/expected-corpus.json`, independently
checks canonical locations and the installed verifier, then reimports and
checks unchanged-source skipping. Each subprocess has a 600-second deadline
and a retained failure log under `.tmp/rust-import-tests`. The test fingerprints
all input files before/after; it never changes sources.
The configured ClamAV engine is required, just as for the other
scanner integration tests. Expectations include infected mail without fixing
its signature-dependent destination; new signatures cannot excuse lost bytes.
`make update-corpus-expectations` explicitly selects the Rust golden-maintenance
test and regenerates the expected JSON only after those integrity checks.
Review the generated diff: updating a golden file is not proof of correctness.
Git-ignored local additions are recorded separately in a private expectation
file under the checkout root; neither their mail nor their subjects belong
in the public fixture manifest. CI uses the same test on its tracked directory.
New or missing files, wrong per-source message membership, and changed exclusion
counts also fail even when the overall canonical message set is unchanged.

The reported September 7 apparent loop was a completed 208-second import:
`email-korean-bad-encoding.eml` locally contained 6,884 MBOX records (82 MiB),
despite its suffix. Content-based MBOX recognition takes precedence over `.eml`;
the worker legitimately stays on that path while advancing through messages.

[`END_TO_END_TESTING.md`](../END_TO_END_TESTING.md) defines the archive-lifecycle,
browser-acceptance, native-WKWebView, and optional XCUITest layers, including
which layer owns macOS menu-bar verification.

The four focused `tests/data/writer-preservation/2024/*.eml` samples retain the
requested wire bytes, including CRLF, invalid UTF-8 and absent final newlines;
Git text conversion is disabled for those files. The committed year component
also lets the full corpus importer resolve their missing dates; public corpus
expectations include the four messages and their fixture README. `test_portable_writer.py`
checks repeated-source idempotence, separate hashes for the same Message-ID,
path-year fallback and verified retrieval. The existing GUI-import acceptance
in `e2e_tests/test_ingest_verify.py` imports the same files through the real
application service, searches and renders each message through `GuiApi`,
exports byte-identical EML, and runs the installed verifier under isolated
Python. Run both with `make test-python-writer
PYTEST_ARGS=e2e_tests/test_ingest_verify.py::test_gui_import_uses_typed_ingest_service_without_a_subprocess`.
This focused storage test explicitly records unscanned mail; it does not claim
ClamAV routing or native-window acceptance.

Tests use small, hand-authored MBOX, Babyl, and EMLX fixtures covering mboxrd
quoting, bounded terminal-preamble and MMDF-framed MBOX, silent empty/metadata
files, MBOX-formatted files under Maildir paths, trimmed Received medians and
Date outliers, exact MBCP exclusion,
`From XXX` unwrapping, parser registration, missing IDs,
same-ID/different-content messages, autosaves,
duplicate source trees, interruption recovery, and infected routing. The EICAR
signature is assembled from fragments only in a temporary test source and that
file is deleted immediately after ingest; the repository contains only a safe
message template. Tests assert message identities and bytes, not only record
counts. Small synthetic rollover fixtures and recorded scanner-failure routing tests
cover those paths; EICAR checks actual scanner version evidence. The separately runnable `make test-e2e` target starts a fresh
CLI ingest with the real embedded ClamAV engine, includes a source message
without a final newline, requires checkpoint publication, and invokes the
installed standard-library-only verifier under isolated Python.

`make test` runs the ordinary test tree. `make check` first requires clean lint
and type analysis, then runs that tree, the separate end-to-end suite, and website
validation. The tracked source corpus has enough messages to
exercise complete scoped searches and rich MIME behavior.
`make test-e2e` drives
the complete interface in headless Chromium while binding every bridge method
to the real Python service and disposable test archive. It therefore works
without a visible desktop on macOS and Linux. `make test-native-gui` separately
drives a hidden Cocoa/WKWebView window on macOS against a purpose-built
one-message derived archive to retain the native bridge boundary without
ClamAV or the full lifecycle fixture. This explicit local development target is
excluded from `make check` and CI/CD; it retains its phase report and any
timeout sample in the ignored checkout-root diagnostics directory selected by
`NATIVE_GUI_ARTIFACT_DIR`. Making native behavior a
required gate would need a logged-in Mac and XCUITest/XCUIAutomation. `make test-bagit`
validates the database-independent three-message fixture and corruption cases.
The installed `verify_mail_archive.py
DIRECTORY` performs read-only validation of a supplied bag. The first acceptance run is against a copied
small subset of `SLG Mail`, followed by a full read-only inventory comparison
before any canonical archive is published.

`make test-pdf-mail` runs the real Poppler text extractor against the six-page
`tests/data/sipbadmin.pdf` scan and the human-reviewed
`tests/data/sipbadmin.mbox` ground truth. It verifies four printed messages on
pages 2 through 5, explicit exclusion of non-message pages 1 and 6, the page-2
handwriting flag, reviewed subjects, generated MBOX structure and provenance,
and byte-for-byte source-PDF immutability. Ordinary pytest skips this focused
integration test when `pdftotext` is unavailable; the focused Make target
requires it and fails clearly.

## Developer validation gates

Scanner deadline and helper-execution regressions use real POSIX subprocesses
and skip the FIFO-blocking case on Windows; `make test-windows-scanner` covers
the Windows inherited-owner lifecycle separately. Pages release-trigger checks parse YAML rather than relying on
indentation, accepting PyYAML's YAML 1.1 interpretation of an unquoted `on` key.

Release preflight checks out the event's `github.sha`, verifies the tag still
targets it and checks `main` ancestry, then runs the standard-library
tag/version validator through `make release-tag-check` with `uv --no-project`.
Both macOS packaging and the reusable Rust reader matrix require successful
preflight; macOS checks out its validated commit. Assembly retains its own
identity checks. Real Git fixtures reject lightweight tags, version/commit
mismatches, moved tags and unmerged commits; workflow checks enforce the event
checkout and both build dependencies.
Only afterward do build jobs install project dependencies and smoke artifacts.
The website header and navigation wrap without positional hiding rules.
Browser geometry checks allow pixel rounding; GUI selection assertions locate
the live virtual-table row by message ID after asynchronous preview redraws.
`make test-website-navigation` checks the actual header and CSS at mobile,
tablet, and desktop widths, including reordered links, in headless Chromium.

The shared pr-to-ready source generates the repository skill and Copilot
instructions. Its preflight compares proposed changes with open PRs, active
tasks, and local unpublished work, including semantic and shared-resource
overlap. Potential conflicts require a concrete list and coordination plan plus
explicit user approval; the ledger records the approved scope and subsequent
checks before integration or publication. Separate worktrees do not waive this
gate. Copilot review requests use `gh pr edit <number> --add-reviewer '@copilot'`
as `simsong`, followed by restoration of `simsong-agent` for all other writes.
Review-request timeline or reviewer evidence verifies the request; no browser
control is used. Before handoff it inventories task checkouts, reconciles intended
uncommitted changes and unpublished commits into the delivery branch, and records
the resulting commit or evidence of inclusion/supersession for each checkout.
Preservation elsewhere is not a completed integration. Its ten-minute heartbeat
changes from review monitoring to post-merge cleanup after handoff. Cleanup
fetches/prunes, verifies GitHub merge state and current-main inclusion,
checks tracked/untracked/ignored files, and
removes only safe task-owned worktrees and represented local branches. It stops
on completion or a reported preservation decision. Repository Claude entries
are generated regular wrapper files, not directory symlinks.
The explicit handoff cleanup step records each task checkout's disposition.
This repository retains unmerged checkouts unless the user authorizes earlier
removal. Such removal verifies publication against the matching PR, preserves
non-rebuildable artifacts with hashes, and checks active use before non-force
worktree removal; branch refs remain until merge verification. Retired checkout
entries are removed from the shared skill inventory to keep synchronization valid.

The ordered [validation requirements](../requirements/VALIDATION.md#developer-validation-gates) define the required checks and failure handling.

### Desktop review follow-up

Packaging follows the [desktop portability requirements](../requirements/VALIDATION.md#desktop-review-follow-up).

### Writer and desktop review boundary

Archive writing is supported on POSIX and Windows. Windows leases use pinned
non-reparse directory handles and `LockFileEx` (see
[Shared Python Windows desktop](DESKTOP.md#shared-python-windows-desktop)). POSIX acquisition pins the archive/status directories and opens lock files
relative to directory descriptors without following links. Lock files must be
regular, single-link files. New targets are created under a parent-directory
creation lock before acquiring the archive lease; creation diagnostics may leave
`.mailarchiver-create.lock` in the parent. Its presence alone does not lock anything.
GUI creation rechecks destination emptiness under the lease. File New proceeds
into Import, About reports the active archive volume, and publication refreshes
mailbox-only queries as well as text queries.

### PR review validation follow-up

Document identity, publication and closure follow the [desktop review requirements](../requirements/VALIDATION.md#pr-review-validation-follow-up).

An Ingests child window retains document routing after all search windows close.
New Search and Ingests use that document directly; Import creates a new search
owner when needed.

Integrity hash-standard versions must be JSON integers. The standalone verifier
rejects boolean, floating-point, and string alternatives without coercion.
