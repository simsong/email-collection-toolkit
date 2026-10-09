<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Windows Rust application handoff

Prepared 2026-10-04 for the Codex instance working inside the Windows VM.

Current builds require Rust 1.99+; `rust-toolchain.toml` selects 1.99.0 with
rustfmt and Clippy. Dated validation checkpoints below retain their historical
compiler versions and test results.

## Windows continuation checkpoint (2026-10-05)

Integrated baseline: `5f7bab67f86d951080b15a78766c956e55f6d351` on
`work-rust-gui` in `.tmp/windows-rust-gui`. Earlier local work is preserved in
stash `963db2f8854e20ca95ed0e527f7d271190525c5e` while integration is published.
The merged #151 code and its two acknowledged import limitations below remain.

Native Windows ARM64 uses Rust/Cargo 1.99.0, Windows build 26200, MSVC 14.44,
Windows SDK 10.0.26100.0, and installed WebView2. The actual navigation and IPC
callbacks report `http://ect.localhost/index.html`; exact-origin checks remain
in force. The native smoke uses WebView2 CapturePreview, not Chromium or a
whole-desktop screenshot. It exercises a 1,600-result synthetic archive with
spaces/non-ASCII in its path, staged previews, six sort orders over real IPC,
scroll paging, selection during search, replacement/clear cancellation, About,
Preferences save, resizing, find-in-message and quit during unfinished search.
It verifies the archive inventory and hashes afterward. Programmatic keyboard
events validate frontend handlers, not physical keyboard delivery or Alt-F4.
Multiple monitor/DPI configurations and physical native menu operation still
need manual acceptance. Windows importing and installers remain unimplemented.

Use the user-approved Cargo workflow directly in PowerShell; GNU Make is not
installed on this VM. macOS Makefile targets wrap the same Cargo commands:
`cargo reader-build`, `cargo reader-check`, `cargo reader-native-check`, and
`cargo run-ect --archive PATH`. The macOS native smoke and ordinary macOS CI
are retained. Windows Actions runs are explicit dispatch/release only, in
accordance with [DEVOPS.md](DEVOPS.md). Full installer/appcast work is pending.
See [WINDOWS.md](WINDOWS.md) for build/run commands.

Windows validation at this checkpoint: `cargo reader-check` passed 16 tests;
feature-enabled Clippy passed with warnings denied; `cargo reader-native-check`
passed the native Windows test (the macOS-only test is compiled out here);
`cargo reader-updater-check` passed against the verified ARM64 WinSparkle DLL.
The selected WebView2 runtime reported `154.0.4258.53`. Retained local evidence
is `.tmp/rust-gui-native-windows/run-MCNbFg`, including the quit-during-search
marker, PNG, logs, and archive hash inventory. The [native Windows screenshot](images/rust-gui-windows-arm64.png)
was visually inspected; it contains only synthetic messages.

Direct `uv` equivalents passed Ruff with zero diagnostics, scoped Pylint, ty,
and Pyright, and 60 workflow/signing tests (two macOS-only tests skipped).
`make ruff` and the full `make check` could not run because Make is absent.
Python interoperability/browser tests, fresh website screenshots, macOS runtime,
and Linux runtime were not exercised on this VM. The inherited macOS CI remains
the macOS validation path. Installer signing and live appcast update delivery
are not covered by the DLL ABI/key-parser test.

## Assignment and definition of success

Continue the working macOS Rust prototype on Windows, retaining one Rust core
and the existing HTML/JavaScript/CSS interface. The user is committed to macOS,
Windows, and Linux and wants minimum total implementation and maintenance work.
The immediate task is to make the Windows Rust reader work as well as the
current macOS Rust reader. The overall destination is the functionality of the
current macOS application, including imports, in a Rust/HTML/JavaScript/CSS
application. A working search window is an intermediate milestone, not full
application parity.

Work incrementally. First establish the Windows native shell and existing reader
behavior with evidence. Then track the remaining product features against the
macOS application. Preserve macOS behavior while implementing Windows support;
keep Linux in mind without claiming Linux support from a Windows build. Do not
restart the UI in another framework merely because older planning documents
discuss Dioxus or Tauri. This branch already has a working Wry/Tao implementation
that reuses the production frontend. The earlier egui implementation remains a
comparison tool, not the UI to use as the Windows parity reference.

Read `AGENTS.md`, `doc/requirements.md`, and `doc/implementation.md` before
changing behavior. Read `doc/RUST_GUI_EXPERIMENT.md` for the reader's current
limits. The requirements and source take precedence over historical plans.

## Repository, branch, and other work

The canonical repository is
<https://github.com/simsong/email-collection-toolkit>.
The older `simsong/mail-archiver` remote redirects there. The publication branch
for this prototype and this handoff is **`work-rust-gui`**. Its starting main
revision was `535a172384d49ad336c61258d51f4d8d5a95aa5f`. Use the published commit
containing this document, not that starting revision, as the Windows baseline.
Record the full checked-out SHA before testing; branch heads can advance.

For a fresh Windows checkout, after obtaining the normal Git tools:

```bash
git -c core.autocrlf=false clone --branch work-rust-gui https://github.com/simsong/email-collection-toolkit.git
cd email-collection-toolkit
git status --short --branch
git rev-parse HEAD
git remote -v
```

Use a short path on the VM's local Windows disk, such as
`C:\dev\email-collection-toolkit`. Avoid VMware shared folders, OneDrive, WSL
mounts, and network shares for the initial baseline. Do not copy the Mac's
virtual environment, build tree, archives, or credentials into the VM. Preserve
fixture line endings; automatic CRLF conversion is inappropriate for byte-level
mail preservation tests.

For an existing checkout, inspect dirty work before switching or updating it.
Fetch the exact branch and compare revisions; do not reset or clean somebody
else's changes. Query **all open PRs** before starting branch work and report
their base/head branches and overlap, as required by `AGENTS.md`.

PR [#151](https://github.com/simsong/email-collection-toolkit/pull/151)
merged into `main` as `945ffa40c9d0e8fbff949dff4039f2493fdadd6d`.
That main revision is integrated into `work-rust-gui` for PR #153 by merge
`85c9b9626fb6845f311438a9dd943a73e81bb19f`.
The merge retains both Rust-reader and Python shutdown release notes; Makefile,
README, requirements, and implementation documentation combine both changes.
The Rust shell remains read-only and separate from the Python import lifecycle.
Preserve the macOS code and its CI job while adding Windows-specific support.

Two acknowledged Python import defects remain inherited from #151; this merge
is not a fix for them: converter processes can survive forced owner exit
([converter thread](https://github.com/simsong/email-collection-toolkit/pull/151#discussion_r4173626875)),
and registry compatibility checking can precede explicit `--reprocess` after
a first-ingest crash
([reprocess thread](https://github.com/simsong/email-collection-toolkit/pull/151#discussion_r4173626902)).
Neither is exercised by ordinary read-only Rust search. They remain relevant
before claiming import/shutdown parity; do not infer their resolution from #151's
merge or weaken the recovery requirements to accommodate them.

Separate, **unpublished and uncommitted** work exists on the Mac in
`.tmp/rust-import-verification`, branch `codex/rust-import-verification`.
It introduces `rust/archive-verifier` and Rust-owned import/database end-to-end
tests. It is deliberately preserved separately and is **not in this prototype
commit**. Do not assume the crate or `make test-import-e2e` exists in your clone.
Coordinate integration when that work is published; do not invent a second
independent verifier implementation without first checking its availability.
Earlier partial validation of that work does not validate this branch.

## What is implemented here

The Cargo workspace includes `rust/mailsearch-gui`, package `mailsearch-rust`.
Its binaries are:

| Binary | Role |
| --- | --- |
| `mailsearch-webview` | Default Wry/Tao native shell; also real JSON dispatcher with `--rpc`, and search timing probe with `--probe`. |
| `mailsearch-rust` | Earlier eframe/egui reader; also synthetic fixture creation and headless search/read smoke mode. |

The running Rust reader needs no Python process or HTTP server. Python remains
in the repository for the existing application and interoperability/browser
test harnesses. This is not a completed Python removal or a Rust import engine.

| File | Responsibility and porting relevance |
| --- | --- |
| `rust/mailsearch-gui/Cargo.toml` | Dependencies and Rust 1.99 minimum. Wry 0.57 and Tao 0.37 support macOS and Windows; snapshot dependencies are feature-gated. |
| `rust/mailsearch-gui/src/bin/mailsearch-webview.rs` | CLI modes, native window/event loop, embedded asset protocol, navigation/IPC trust checks, bounded foreground worker channel, reply delivery, close/quit. |
| `rust/mailsearch-gui/bridge.js` | Request IDs and promise resolution; exposes the compatibility name `window.pywebview.api` without running Python; disables unported controls. |
| `rust/mailsearch-gui/src/bridge.rs` | Real frontend API dispatcher, asset allowlist, search grammar, parameterized SQL, selected-message representation, previews and regression tests. |
| `rust/mailsearch-gui/src/search.rs` | Independent search worker, cancellation generation, preview acknowledgements, complete ordered IDs and result pages. |
| `rust/mailsearch-gui/src/lib.rs` | Read-only SQLite opening, schema checks, safe archive paths, bounded MBOX reads, SHA-256 recovery/verification and MIME display. |
| `rust/mailsearch-gui/src/worker.rs` | Earlier egui reader worker. |
| `rust/mailsearch-gui/src/main.rs` | egui presentation and real headless widget interaction test; smoke/demo commands. |
| `rust/mailsearch-gui/src/demo.rs` | Three synthetic messages using the real archive/search schema SQL; refuses an existing output directory. |
| `gui/index.html`, `gui/app.js`, `gui/style.css` | Shared application frontend. The JS opts into incremental search only when the Rust methods exist. Python retains its existing API path. |
| `tests/test_rust_gui.py` | Python-created archive compatibility; executes the Rust reader and verifies unchanged archive bytes. |
| `tests/test_rust_webview.py` | Real Rust RPC dispatcher behind the actual frontend in Chromium; searches, sorts, pages, selects, cancels, resizes, finds text, and checks fixity. |

The workspace lockfile includes GUI dependencies and updates to shared
dependencies. Use `--locked` through Makefile targets; avoid introducing a second
lockfile or blindly refreshing dependencies while diagnosing Windows failures.
Bundled SQLite avoids a separately installed SQLite library but still requires
the native C build toolchain. eframe remains a normal crate dependency, so even
the current webview build resolves/builds the comparison UI dependencies.
Feature-gating that comparison is a possible later reduction, not a prerequisite
for reproducing this baseline.

## First Windows implementation boundary

`mailsearch-webview.rs` enables `native()` on macOS and Windows.
Other systems call an explicit unsupported-native-shell stub. Merely
building an EXE, passing RPC tests, or opening the egui app cannot establish
Windows webview support.

Enable the Windows native shell and appropriate target dependencies while
preserving the macOS path. Keep platform differences small and explicit.
Inspect the pinned Wry/Tao source and official documentation for the precise
API; avoid assuming a different release's examples match the locked version.

The particularly important trap is the **custom-protocol origin**. The current
macOS code loads `ect://localhost/index.html` and admits exactly that string in
both navigation and IPC checks. Wry 0.57 documents that on Windows a registered
custom-protocol navigation becomes
`http://ect.localhost/index.html`, or HTTPS when its Windows HTTPS-scheme option
is enabled. See the pinned Wry source documentation for `with_url`,
`with_custom_protocol`, and `WebViewBuilderExtWindows::with_https_scheme`.
Observe the actual navigation URL and IPC request URI in WebView2 before
settling the implementation. They need not have identical transformations in
every callback. Do not turn the check into “allow every URL” to get a window.

Represent the expected local main-document URLs explicitly, with focused tests
for their validation, and check the real callbacks in the VM. Preserve the
asset allowlist; never expose arbitrary filesystem paths through the protocol.
Only the trusted main document may invoke backend operations. External links,
subframes, arbitrary paths, and untrusted mail must not gain access to IPC.
The initialization script already refuses non-top-level frames. Keep MIME HTML
as inert text until a separately tested renderer is implemented; never fetch
tracking resources while opening a message.

Also verify WebView2 initialization, window resizing/DPI handling, keyboard
focus, user-event reply delivery, and native Close/Alt-F4. A black window or
working document with dead controls often means protocol/initialization/IPC
failed; inspect those boundaries before changing the archive engine.

## Tools and paths inside the VM

Use actual Windows, not WSL, for Windows acceptance. Record OS build, CPU
architecture, `rustc -vV`, Cargo version, MSVC/SDK availability, WebView2 runtime
version, shell, and exact Git revision. Rust **1.99 or newer** is required by
the current crate; `rust-toolchain.toml` selects 1.99.0 for the checkout.

Native Windows builds need the MSVC Rust target, the corresponding C/C++ build
tools and Windows SDK, and the WebView2 runtime. Verify what is already installed
before proposing installation. Follow the repository's approval requirement
before installing software or changing machine configuration. Edge being
installed does not by itself verify the WebView2 runtime used by the program.
No Dioxus CLI, Tauri CLI, or Node package installation is needed for this Wry
prototype.

On an ARM64 VM, record whether the executable is native ARM64 or x64 under
emulation. The existing Python Windows setup selected x64 for dependency
compatibility; that is not proof that all Rust targets must use x64. Choose one
explicit baseline consistent with the available compiler/SDK, record it, and
test the other desired architecture separately. Do not silently combine x64
and ARM64 libraries or claim native ARM performance from emulation.

Rust builds/tests use Cargo directly on Windows, as authorized by the user.
The historical Make recipes require a POSIX-compatible shell. `doc/WINDOWS.md` documents the
MSYS2/native-Windows tool split and Python setup, but its earlier Dioxus/Tauri
trial instructions are historical for this task. MSYS2 supplies utilities; the
app must still be built with the intended native Windows Rust/MSVC toolchain.
Inspect the environment inherited by Cargo so the linker and SDK are available.

The current Makefile assumes `OS=Windows_NT` to select `.exe` and passes
`RUST_TARGET_DIR` through Cargo configuration. Check those values in the actual
Windows shell. MSYS paths such as `/c/dev/...` and native paths such as
`C:/dev/...` can behave differently when Make passes a value inside Cargo's
`--config` string. If needed, explicitly supply a native absolute path through
the existing `RUST_TARGET_DIR` override; fix and document a demonstrated path
conversion defect rather than hard-coding the VM path.

Python is needed only for the existing application and Python-based test
harnesses. Use `uv` and the committed lockfile when running them. Do not copy a
macOS `.venv` or use pip to improvise the environment. Playwright Chromium tests
do not exercise WebView2. The browser test currently presses `Meta+f`; verify
and adapt shortcuts for Windows `Control+f` rather than assuming that a macOS
test shortcut establishes Windows behavior.

## Reproduction and Makefile commands

From the repository root, with the appropriate existing tools available:

```bash
make rust-toolchain
make rust-gui-build
make test-rust-gui
```

The last target checks formatting, Clippy with warnings denied, and the crate's
ten substantive Rust tests. It does not open a native window. Build and run a
fresh synthetic demo with an output directory that does not already exist:

```bash
make rust-gui-demo RUST_GUI_DEMO=.tmp/windows-reader-demo
make rust-gui-smoke ARCHIVE=.tmp/windows-reader-demo QUERY=observatory
make rust-webview-probe ARCHIVE=.tmp/windows-reader-demo QUERY=observatory
make rust-gui ARCHIVE=.tmp/windows-reader-demo
```

Create `.tmp` first if absent; the current demo helper creates the destination
directory and creates missing parents. The equivalent Windows launch is `cargo run-ect --archive PATH`. The demo
creation target must not be rerun against an existing directory. Reuse that
fixture for later reader runs, or choose a fresh directory; never delete an
unknown archive to make fixture creation pass.

Search `observatory`, select **Observatory planning** and **Café notes**, then
search `roses` and select **Garden update**. Check text decoding, accents,
mboxrd quoting, headers, search highlighting and HTML-to-text. The garden HTML
contains a script and remote image solely as synthetic negative cases; neither
should execute or fetch. The demo is a reader fixture, not a full BagIt archive
and not evidence of import correctness.

Additional existing gates:

```bash
make lint
make types
make rust-check
make test-rust-gui-interop
make test-rust-webview
make test-e2e
```

`make lint` runs Ruff then Pylint; `make types` runs ty then Pyright. Keep that
order before the Python test stages. `make rust-check` covers the entire Rust
workspace, not only the reader. Some broader workspace tests have OS/external
prerequisites; record actual skips/failures. Do not suppress diagnostics to make
the port appear green. `make check` is the full project aggregate and includes
more scanner/importer prerequisites than the standalone reader needs.

The Python-created-archive tests may initially fail on Windows because their
fixture uses the real Python writer, which currently rejects Windows archive
writing. That is a distinct boundary from a Rust reader failure. The Rust-only
demo supplies an initial native reader fixture. For cross-platform equivalence,
use a purpose-made archive copied from macOS, with a file inventory/hashes
verified before and after transfer, or make the real fixture writer portable
with proper locking tests. Do not bypass writer locking in a test and call that
production ingest support.

`RUST_GUI_BINARY` and `RUST_WEBVIEW_BINARY` select the freshly built executables
for the corresponding Python targets; Make sets them. `ARCHIVE` chooses the
reader input, `QUERY` the smoke/probe query, `RUST_GUI_DEMO` a new fixture
destination, and `CARGO`/`RUST_TARGET_DIR` allow tool/output overrides. See README
and Makefile comments. The smoke mode prints selected synthetic mail; the probe
prints counts/timings without message text. Never capture private mail in logs
or screenshots.

## Search behavior that must survive the port

The webview path supports FTS words/quoted phrases plus literal `subject:`,
`from:`, `to:`, `cc:`, `bcc:`, and `any:` filters joined with AND. It supports
date/subject/sender sorting in both directions. It does not yet implement the
Python application's full grammar, date selectors, name resolution, or
autocomplete. Do not silently widen or reinterpret the query language during
the native-shell port.

The foreground worker owns message reads and short control requests. A second
connection/worker performs searches. Two ordered 512-entry catalog preview
windows are acknowledged only after the frontend paints them, followed by one
comprehensive query over the remainder. Sparse windows can contain no matches
and must still advance. Tied sort values use a message-ID tie-breaker.

The backend retains the full ordered ID list. The frontend initially loads at
most 1,024 display rows and fetches later 512-row pages on scrolling. Complete
counts and loaded-row counts differ intentionally. Selection remains available
during a long search, and completion preserves it. Changing query/sort, clearing,
or closing cancels obsolete generations; late results must not repopulate a new
search. Preview work has a 15-second limit and the comprehensive query a
120-second limit. Errors must identify incomplete results, never label them
complete. The egui comparison instead has its older 100-result/three-second
limits; do not accidentally use that as the webview acceptance specification.

## Preservation and failure semantics

Open only purpose-made fixtures or an explicitly authorized idle archive. No
real canonical ingest is authorized by this handoff. Never modify source
mailboxes, Apple Mail stores, IMAP/Gmail state, or input archive bytes.

The reader opens existing schema-version-1 `archive.sqlite3` and
`search.sqlite3` read-only. Database paths and selected MBOX paths must resolve
inside the archive. Windows path handling needs explicit checks for drive
letters, separator forms, Unicode, spaces, case behavior, junctions/reparse
points, and attempts to escape the archive. Use native path APIs; do not weaken
the containment check to a string prefix. Add tests for demonstrated platform
differences, with appropriate handling for privileges needed to create links.

Canonical MBOX bytes and SHA-256 define message identity. The reader reverses
supported mboxrd/simple legacy storage transformations and displays a message
only if an original-byte candidate matches the recorded digest. Never repair,
normalize, or rewrite an input to make a mismatch disappear. A 16 MiB record
limit and 256 KiB per-field display limit are intentional prototype constraints.
Complex legacy mboxo ambiguity may produce an explicit error.

WAL-mode databases are refused before opening SQLite to avoid creating sidecars.
The reader does not recover hot journals or guarantee a cross-database snapshot
during simultaneous imports. Preserve that limitation until there is a tested
design. Test unsupported schema, missing files, truncated/modified records,
out-of-range offsets, malformed MIME, invalid encodings and read failures.
Compare file inventory and hashes before and after reading, including sidecars.

`processing.sqlite3` contains manual identity/tag decisions that are durable
user data. Do not describe every database as disposable or omit it from backups.
Later import work must preserve original RFC 5322 bytes, SHA-256, quarantine
routing, source observations, idempotence, autosave exclusion, rollover, and
message-boundary interruption/recovery. Scanner errors cannot silently drop
mail. Tests must exercise real behavior rather than mocks or only compilation.

## Parity matrix and next stages

| Area | Current macOS Rust prototype | Windows acceptance / later work |
| --- | --- | --- |
| Native shell | User reports working Wry/Tao window. | Real WebView2 launch, resize/DPI, focus, request/reply and clean close; no Python runtime. |
| Search and selection | Staged/cancellable search, six sort combinations, previews and paging. | Same IDs/order/counts on identical fixtures; no duplicates, omissions, stale updates or blocked reading. |
| Message display | Hash-verified text/headers; MIME decoding; HTML-to-text. | Same canonical hashes and meaningful decoded content; errors remain visible and bytes unchanged. |
| UI interaction | Shared layout, pane resizing, highlighting, find-in-message. | Ctrl shortcuts, keyboard navigation, clipboard, high DPI, window lifecycle; native evidence. |
| Advanced reader features | Original-folder filtering, date/name resolution, suggestions and saved filters unported. | Track against Python/macOS behavior; implement after basic reader parity. |
| Attachments and output | Rich HTML/images, attachment operations, save/export/print and extra windows unported. | Restore deliberately with byte/security/OS integration tests; retain unavailable controls until implemented. |
| Imports and processing | Not implemented in this Rust GUI. | Full product parity requires safe writers, source adapters, scanner, progress, resume and processing/manual-state preservation. |
| Distribution | Development Cargo executables only. | Repeatable Windows bundle/installer, WebView2 dependency handling, clean-machine tests, signing/update design. |
| Linux | Native shell disabled. | Future Linux toolkit/runtime packaging and native acceptance; do not infer it from portable core tests. |

The Python implementation still has a Windows writer guard in
`src/mailarchiver/writer_lock.py` and an unconditional POSIX `fcntl` import in
`src/mailarchiver/plugin_configuration.py`. GUI code includes macOS integrations.
The scanner now uses embedded libclamav; historical instructions about a
POSIX-only daemon scanner are stale. Inspect the actual implementation before
choosing the Windows scanner/process strategy. Do not remove platform guards
until their required locking/recovery semantics are implemented and tested.

The agreed test migration starts with **import/database end-to-end tests in
Rust**. Browser migration was deferred. Python interoperability harnesses in
this prototype are therefore not evidence that the test migration is finished,
nor a reason to combine a browser-harness rewrite with the Windows shell port.

For OST, see
[#152](https://github.com/simsong/email-collection-toolkit/issues/152).
PST and OST share the PFF family, but accepting the `SO` header alone is not
universal OST support: both the application and pinned dependency validate
headers, and newer variants have additional format differences. Qualify support
with genuine fixture extraction and preservation tests; do not conflate Windows
support with completed OST support.

## Evidence at publication and evidence to collect in Windows

The user reports the native macOS prototype working. Publication validation on
macOS reran the following successfully: `make test-rust-gui` (ten Rust tests),
`make rust-check` (workspace format/Clippy/tests), `make lint types` (zero lint
or type diagnostics), `make test-rust-gui-interop` (one real compatibility test),
and `make test-rust-webview` (one real backend/browser interaction test).
`make test-e2e` passed 34 tests with seven opt-in native macOS checks skipped.
The publication host used Rust 1.98.1 on `aarch64-apple-darwin`. The full
`make check` aggregate and packaged-app gates were not rerun for this handoff.
The workspace's ordinary Rust suite leaves its opt-in ClamAV integration test
ignored. Website screenshots were regenerated with `make website-screenshots`
and the site built/captured with `make website-preview-screenshots`; the homepage,
Searching and Importing views and the Rust browser screenshot were inspected.
These use synthetic messages only. Existing ClamAV definitions produced an age
warning during synthetic screenshot ingest, not a scanner failure.

This evidence does not establish native Windows behavior, a Windows package,
Linux support, or a complete Rust rewrite. The publication PR/CI should be
checked at its exact current SHA. A follow-up adds a `macos-latest` Rust GUI
matrix job and `make test-rust-gui-native`: it creates a synthetic archive with
the CLI, searches/displays through actual WKWebView IPC, captures a native PNG,
and verifies archive fixity. See README for its uploaded evidence. The original
static/Python jobs remain on `macos-15`; the two focused Rust/Python
interoperability targets were opt-in and
not explicitly called by the aggregate CI target. A default pytest run skips
them when their binary environment variables are absent. Add appropriate
Windows coverage once prerequisites and native execution have been established;
do not label skipped tests as passing coverage.

For each Windows milestone, record the SHA, environment/architecture, exact
Makefile commands, pass/fail/skip counts, elapsed time, and synthetic screenshots
or diagnostics. Verify the visible WebView2 app, not only headless Chromium.
Compare before/after file inventories and hashes. Exercise two preview windows,
long-search responsiveness, all six sort combinations, multiple result pages,
replacement/clear cancellation, message selection during search, error recovery,
and closing during work. Check a fixture path with spaces and non-ASCII text.

For full Windows delivery, additionally demonstrate create/import/search/read,
deduplication and re-import idempotence, malformed message retention, EICAR
quarantine, scanner failure handling, rollover, interrupted import recovery,
preserved manual state, and a clean-machine packaged launch without developer
tools. Do not publish a release or ingest private mail solely to obtain evidence.

## Git delivery and collaboration

Use `simsong-codex` for GitHub writes and verify CLI and push identities
separately. Signed commits use
`Codex AI Assistant <simsong+codex@acm.org>` for author and committer. Follow
the repository's exact PR/publication rules; no force pushes, merging, releases,
or destructive cleanup are implied by this handoff. The personal `simsong`
account exception is solely for requesting Copilot review, then restore the
Codex account. Do not copy signing secrets between machines.

Keep the shared frontend and archive contracts together. Prefer a small Windows
adaptation over a forked UI or duplicate archive engine. Use Makefile targets
for all routine validation, document new environment variables, add the required
purpose/operation header to new source files, and update requirements,
implementation notes, and release notes with behavior changes. Preserve other
developers' dirty branches/worktrees and report exact blockers and unverified
behavior. The result should be easy for the macOS and future Linux tasks to
review and reuse.
