<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Rust reader experiment

The default experiment hosts the existing HTML/CSS/JavaScript search interface
in a Rust-owned macOS window (Wry/Tao). Separate Rust workers handle search and
verified message decoding, keeping message reads usable during a long search. It reuses ECT's catalog, FTS index and canonical MBOX
files without a Python process, HTTP server, or schema migration.

The earlier egui prototype remains available with `make rust-gui-egui` for
comparison. `make rust-gui` now opens the familiar production-style interface.
Close the old prototype and rerun the command to try the new shell.

Continue Windows implementation with [WINDOWS_RUST_HANDOFF.md](WINDOWS_RUST_HANDOFF.md),
which records the architecture, native-shell boundary, validation commands,
preservation constraints, and remaining work toward full macOS feature parity.

## Try it

From the repository root, using Rust 1.95 or newer:

```sh
make rust-gui ARCHIVE="/path/to/existing/archive"
```

Or use the purpose-made synthetic fixture:

```sh
make rust-gui-demo
make rust-gui ARCHIVE=".tmp/rust-gui-demo"
```

The fixture already created during development can be reused with the second
command. Creation deliberately refuses to overwrite any existing directory.
`RUST_GUI_DEMO` can select a different new fixture path.

Search for `observatory`, then select **Observatory planning** or **Café notes**.
Search for `roses` and select **Garden update** to try HTML converted to text.
An empty query shows search help. Choose the archive using `ARCHIVE` at launch. Close the window or use Command-Q to exit. No installation is needed.

## Scope

- Search supports indexed words, quoted phrases, and `subject:`, `from:`,
  `to:`, `cc:`, `bcc:` and `any:` literal address/subject filters joined with AND.
  Date operators, resolved names and autocomplete have not yet been ported.
- Date/subject/sender sorting, keyboard selection, indexed previews, pane
  resizing, search highlighting, and find-in-message reuse the original UI.
- Search displays matches from two 512-entry catalog windows, then runs one
  comprehensive background query over the remainder. Selection and previews
  use an independent connection. New queries/sorts and clearing cancel obsolete
  SQL and discard stale responses. Preview windows have 15-second limits; the
  comprehensive query has a 120-second safety limit.
- Complete ordered result IDs stay in Rust. Initially up to 1,024 rows are loaded;
  scrolling retrieves additional 512-row pages. The final status distinguishes
  the full match count from loaded rows. Selection operates on loaded rows.
  Completion preserves selection and scroll position; errors retain partial
  results and identify the search as incomplete.
- Original folder filtering, saved filters, extra message windows, imports,
  export, printing and attachment operations remain unported. Controls are
  disabled or show a specific unavailable-operation error.
- Display supports MIME charset/transfer decoding and plain text or HTML-to-text.
  Attachments, rich HTML, images, export, and imports are not implemented.
- Every displayed message must match its catalog SHA-256. Read failures are
  shown in the window; canonical bytes are never repaired or rewritten.
- Records over 16 MiB are refused. Display text is limited to 256 KiB per field.
- Current mboxrd and simple legacy mboxo are supported. Ambiguous mixed legacy
  quoting can require the Python reader; this experiment reports an error.
- Use an idle archive. Hot journals require recovery by the existing application;
  the Rust reader never requests write access. WAL-mode databases are refused
  before SQLite opens them, avoiding shared-memory sidecar creation.
  Simultaneous import snapshots,
  native accessibility, installer distribution, and OS parity are not yet proven.
  The new shell is initially enabled only on macOS.

## Validate without windows

```sh
make test-rust-gui
make test-rust-gui-interop
make test-rust-webview
make rust-gui-smoke ARCHIVE=".tmp/rust-gui-demo" QUERY=observatory
```

The first target drives Search and result selection through headless egui
widgets and the real worker. The second verifies a Python-created archive and
checks every file's bytes remain identical. The smoke command prints the first
selected message through the same worker, so use synthetic mail when recording
logs. These checks do not establish native window appearance or platform parity.
`make test-rust-webview` drives the original page against the actual Rust
backend in headless Chromium and records `.tmp/rust-gui-existing-interface.png`.
The native webview's IPC and window behavior still need a manual trial.
`make rust-gui` and `make rust-gui-egui` launch visible apps; tests do not.
