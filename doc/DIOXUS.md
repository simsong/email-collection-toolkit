# Compiled desktop UI: Dioxus preferred, Tauri alternative

Decision: 2026-09-10. The preferred compiled desktop experience uses **Rust with
Dioxus Desktop's system-webview renderer**. Only the UI moves to Rust. Ingest,
MIME processing, search, SQLite, scanner orchestration, archive locking, and
canonical MBOX/SHA-256 preservation remain in Python. **Tauri remains a possible
alternative** for the compiled desktop experience.

## Preferred direction and current status

The current application is Python/pywebview with HTML/CSS/JavaScript and
macOS-specific integration. `make gui` and `make dmg` still run/build that
implementation. This decision adds no Rust application, Cargo workspace,
Dioxus build target, Python worker protocol, or Windows installer. Existing
pywebview documentation describes the baseline, not the future compiled UI.
Retain the working macOS application during migration.

Dioxus Desktop runs Rust natively and renders through Wry and the system
webview: WKWebView on macOS and WebView2 on Windows. This decision selects that
webview path, not a custom native renderer. Rust components will own UI state,
events, and desktop coordination. CSS, images, and browser rendering may be
reused; HTML/JavaScript does not become Rust automatically. Tabulator may remain
behind a JavaScript adapter during migration, with one owner for its DOM subtree.
[Dioxus desktop documentation](https://dioxuslabs.com/learn/0.7/guides/platforms/desktop/),
[JavaScript integration](https://dioxuslabs.com/learn/0.7/essentials/ui/escape/).

## Tauri alternative

Keep Tauri available if a prototype demonstrates a better fit for existing
HTML/CSS/JavaScript reuse, desktop integration, or Python-worker packaging.
Tauri provides a compiled Rust host around a web frontend and supports bundled
external binaries. It would let us retain more JavaScript UI logic; adopting
that approach would revise the Rust UI scope while keeping the Python engine.
See [Tauri architecture](https://tauri.app/concept/architecture/) and
[external binaries](https://tauri.app/develop/sidecar/).

Compare a representative search-and-ingest window on macOS and Windows before
changing the preferred framework. Assess message isolation, progress and
cancellation, native integration, testing, and installed-app packaging. Both
options must satisfy the same Python boundary and acceptance requirements below.
The implementation sequence describes the preferred Dioxus path; Tauri-specific
tooling and tests would be documented if adopted. Neither frontend exists yet.

## Python boundary

The planned integration is a bundled Python worker serving typed local
requests, results, progress, cancellation, and errors to the Rust UI. Python
retains authoritative archive state and all writes. Rust must not become a
second archive writer or independently update SQLite. Pydantic models remain
the Python boundary types; corresponding Rust types must be checked against
the same contract. Framing, transport, version negotiation, and worker packaging
require implementation and validation; they are not existing APIs.

Support paginated searches, multiple documents/windows, cancellation, and worker
failures without transferring an entire archive into the UI. Define shutdown
and UI-crash behavior so interrupted imports retain durable status and remain
recoverable. The worker requires no cloud service or mail upload. The existing
loopback server serves assets only, not a Python service API.

## HTML and desktop behavior

Keep untrusted message HTML isolated from the privileged application document
and Rust/Python bridge. Preserve the sandbox, blocked scripts and remote
resources, approved-link handling, source bytes, and attachment confirmations.
Never insert message HTML into the privileged UI with `dangerous_inner_html`.
Validate selection, printing, MIME/attachment display, export/drag, large result
lists, high-DPI layout, clipboard, dialogs, activation, and import-aware quit.

## Windows priority and packaging

The next platform priority is **Windows with full ingest**, using the same
Python engine and behavior as macOS. Linux/snap delivery is deferred; framework
portability does not establish application support. Windows still needs native
writer locking, reparse-point handling, scanner portability, and crash/recovery
validation. The UI migration neither removes those requirements nor adds input
formats such as PST/OST.

Package the compiled Rust UI with Python, its dependencies, GUI assets, and the
validated worker entry point. Users must not install Rust, Python, uv, or build
tools. Handle WebView2 prerequisites on Windows. Dioxus bundling does not
automatically package or supervise our Python worker. PyInstaller remains the
current macOS packager and a possible worker packager, not the Rust UI compiler.
See [Windows setup](WINDOWS.md) and [current macOS delivery](MACOS_DISTRIBUTION.md).

The initial Windows customer artifact targets x64; validate on Windows x64 and
under emulation in the ARM64 VM. Native ARM64 artifacts require separate
dependency and packaging validation. Rust and Python process architectures are
separate choices; an embedded-Python alternative would require matching ABIs.
Pin Rust/Dioxus versions and dependencies when introducing the frontend.

## Implementation and acceptance sequence

1. Make Python ingest safe and testable on native Windows, preserving canonical
   bytes/hashes and source immutability.
2. Implement the typed worker contract and test progress, cancellation, errors,
   worker exit, and interrupted imports.
3. Build one Dioxus window with real search, isolated HTML, and ingest
   progress/cancellation on macOS and Windows.
4. Migrate remaining UI and native integration with behavior parity. Reuse
   fixtures and logical assertions, adapt the test driver, and add native
   Dioxus tests; pywebview smoke tests do not validate the replacement.
5. Add Makefile targets for Rust formatting/linting, tests, Dioxus builds,
   packaging, and installed-app acceptance. Retain Python validation. Release
   only after full-ingest and clean-install tests pass for the declared matrix.

This is the preferred direction, not a report that migration is complete. See
[requirements](requirements.md), [implementation](implementation.md), and
[end-to-end testing](END_TO_END_TESTING.md).
