<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Historical GUI build notes

Archived October 10, 2026. Commands below no longer select supported workspace packages. See [retirement and retrospective](README.md).

## Rust desktop migration

`work-rust-gui` hosts the existing HTML/CSS/JavaScript interface in a Rust
Wry/Tao shell on macOS and Windows. Rust handles staged search, folder filters,
verified MIME/HTML display, attachments and native reader actions. The local
migration build uses a supervised Python archive-service helper for creation,
imports, recovery, owner rules and identity edits; it never starts pywebview.
The original Python GUI remains available until native migration acceptance.
Rust 1.99+ is required to build the desktop. `rust-toolchain.toml` selects 1.99.0
with rustfmt and Clippy for this checkout, without changing the global default;
hosted CI also checks its explicitly selected stable compiler. `RUSTUP_TOOLCHAIN`
overrides the checkout's compiler selection for an explicit alternate-toolchain run.
Reading/searching existing archives
needs no Python; archive-service operations require the prepared project Python
environment. Archive writing remains unsupported on Windows.
`ECT_RUST_ENGINE_PYTHON` optionally selects that environment's Python executable;
the default is this checkout's `.venv/bin/python` (`.venv/Scripts/python.exe` on
Windows). There is no HTTP service. See the [migration status](../doc/RUST_GUI_MIGRATION.md)
for implemented controls and outstanding platform/release work.
Use `cargo run-ect --archive "/path/to/archive"` to build and launch the GUI
on Windows or macOS. The macOS Makefile launch target delegates to this alias.
Run `make rust-gui ARCHIVE="/path/to/archive"` (or omit `ARCHIVE` for recent/open/new selection), or create the synthetic fixture
with `make rust-gui-demo` and then run
`make rust-gui ARCHIVE=".tmp/rust-gui-demo"`. `make rust-gui` opens the reused interface; `make rust-gui-egui` opens the
earlier widget prototype for comparison.

`make test-mime-transfer` checks bounded strict/permissive transfer decoding against
the reference libraries. `make test-rust-gui` runs headless Rust and widget tests;
`make check-rust-gui-native-build` compiles native acceptance without opening windows;
`make test-rust-gui-interop` checks a Python-created archive.
`make test-rust-recovery` tests foreground hot-journal repair, elapsed time,
long waits, Abort and later recovery through the real Rust/Python opening path.
`make test-rust-webview` tests the reused page against the real Rust dispatcher
in headless Chromium. `make test-rust-engine` exercises synthetic import, cancellation, resume, writer exclusion and identity edits. `RUST_WEBVIEW_BINARY` selects that dispatcher for the test
and is set automatically by its Makefile target. `ARCHIVE` is the
archive path and `QUERY` supplies words to `make rust-gui-smoke`.
`RUST_GUI_DEMO` overrides the new synthetic fixture destination; creation refuses
an existing directory. `RUST_GUI_BINARY` selects the compiled executable for the
Python interoperability test and is set automatically by its Makefile target.
`ECT_RUST_PREFERENCES_TEST_ROOT` and `ECT_RUST_PREFERENCES_TEST_EDIT` are internal
Rust preference fixtures: a private directory and the font/update edit performed
by a test child. They are not application settings.
Set `ECT_RUST_WEBVIEW_DIAGNOSTICS=1` to log native navigation and IPC document
URLs to stderr while diagnosing the shell. It does not log request bodies or
message contents. Leave it unset for ordinary use.
`make rust-webview-probe ARCHIVE="/path/to/archive" QUERY=words` prints
counts and timings for matching result batches and complete search without
exposing message text. Results are paged on scroll; message reads remain usable
while the separate search worker runs, and replacement queries cancel old work.
`make rust-webview-release-probe ARCHIVE="/path/to/archive" QUERY=words` runs
the same count/timing probe using the optimized build. `make test-search-parity`
compares real Python/Rust search services and consumes the same optimizer cases as
the Rust unit suite: production headers, ordered IDs, counts and useful filtering
indexes. It opens no native windows. The aggregate test gate includes this target.
See [scope and limitations](../doc/RUST_GUI_EXPERIMENT.md).

`make test-rust-gui-native` builds with the opt-in `native-smoke` feature and
opens the actual macOS Wry/WKWebView window. A Rust test creates synthetic EML,
ingests it with the CLI into `Sample.mailarchive`, runs the portable verifier,
searches and selects a message through the native UI, and captures a WebKit PNG.
It checks source/archive fixity and fails on timeout or missing evidence.
`RUST_GUI_ARTIFACT_DIR` chooses the evidence directory (default
`.tmp/rust-gui-native`); each run retains its synthetic archive, logs, hashes and
`rust-gui.png`. CI runs this in the **Rust GUI (macos-latest)** matrix job and
uploads a `rust-gui-macos-latest-<commit>` artifact even on failure. It requires
a macOS GUI session; ordinary reader builds contain no smoke driver.

Native Windows evidence is produced with `cargo reader-native-check` (opens
WebView2 on synthetic mail and exits). `RUST_GUI_ARTIFACT_DIR` selects retained
native evidence; the default Windows directory is `.tmp/rust-gui-native-windows`.
`ECT_RUST_NATIVE_CLOSE_SMOKE` is a feature-gated test switch for quitting during
an unfinished search. Production builds do not embed the smoke driver.
Preferences use `LOCALAPPDATA` on Windows, `HOME/Library/Application Support`
on macOS, and `XDG_CONFIG_HOME` or `HOME/.config` on Linux. Tests isolate these
paths; email archives never contain reader preferences.
On Windows, `APPDATA` locates prior Python update preferences for migration;
Rust continues storing its own reader preferences under `LOCALAPPDATA`.

`ECT_RELEASE_VERSION`, `ECT_RELEASE_BUILD`, and `ECT_RELEASE_CHANNEL` are
build-time metadata generated by `scripts/rust_release_metadata.py` through the
shared release mapper. It also supplies `ECT_UPDATE_FEED_URL` and
`ECT_UPDATE_PUBLIC_KEY` from the common public update metadata. Both adapters
use these inputs; private signing keys never enter a binary. Source builds
without mapped metadata leave updates unavailable. Packaged builds default to
daily checks and their release/preview channel, retaining explicit Python or
Rust update preferences outside archives.
`ECT_WINSPARKLE_TEST_DLL` selects the staged, checksum-verified native DLL for
`cargo reader-updater-check`. `scripts/win/prepare_winsparkle.ps1` stages that DLL
and license notices without a system installation. `CARGO_TARGET_DIR` selects
build output for direct Cargo invocations. For Make targets, set
`RUST_TARGET_DIR` (default: checkout `target/`); Make exports it as
`CARGO_TARGET_DIR`, replacing the inherited environment value.

## Windows development

Start with **[Windows GUI: build and run](../doc/WINDOWS.md)** for prerequisites
and copyable PowerShell commands using Cargo. Native ARM64 builds and WebView2
launch/navigation/IPC have been exercised on the development VM; full native
interactive acceptance remains incomplete. The detailed
[Windows Rust handoff](../doc/WINDOWS_RUST_HANDOFF.md) covers architecture and
remaining acceptance criteria.
Full Windows application parity also requires imports, processing,
attachments, exports, and packaging. The existing packaged application still
uses Python/pywebview. The earlier [Dioxus/Tauri plan](../doc/DIOXUS.md) is historical
context, not a prerequisite to porting this prototype.

The Windows guide also retains historical full-application setup notes.
This is a development reader, not a supported Windows application release.
