<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Rust desktop migration: local testing

The Rust/Wry desktop owns the existing interface, search and message reader.
A transitional Python **archive service** handles import, recovery, options and
identity operations without importing the Python GUI. The project environment
must be available for checkout launches. `make dmg` (also `make rust-dmg`)
creates the primary self-contained Rust application with its private Python
archive service. Release CI must sign, notarize and validate that exact candidate
before publication; a local signed build alone does not establish those gates.
The user performs interaction
testing, including physical drag gestures and window/document coordination.
The [theory of operation](implementation.md#theory-of-operation-rust-desktop-and-python-ingest)
explains how the Rust GUI supervises the private Python ingest process and which
parts run in each language.

From the repository root:

```sh
make rust-gui ARCHIVE="/path/to/archive.mailarchive"
```

Without `ARCHIVE`, the app selects the first valid or recoverable recent archive,
opening foreground recovery when required; missing/invalid entries are skipped.
With no eligible recent archive a welcome page offers opening and, when the
helper supports writes, creation while keeping Dock document events available.
File → New Archive chooses an empty destination. File → Open and
Open Recent open additional reader processes. Closing one closes that window's
engine; other reader windows remain independent. Import jobs belong to the
window that started them, with cross-process writer leases preventing conflicts.
On macOS the Open panel selects `.mailarchive` packages as documents. Explicit
Dock/Finder opens focus a matching archive or open another reader; the local DMG
declares this document support and uses its bundled application icon/name.
Physical selection/drop/display behavior remains for the user to verify.

## Implemented for local trials

| Area | Controls and behavior |
| --- | --- |
| Search | Indexed matching batches and streamed completion; cancellation, paging, sorting, selection; dates, phrases, names/institutions, role-count autocomplete, attachment text, original folders and shared saved filters |
| Reading | Hash-verified source; decoded headers/text, HTML sanitization, CID images, remote-image consent, MIME alternatives/raw source, attachment previews and parent/source provenance |
| Actions | Save message and attachment to new files, macOS verified EML/ZIP drag preparation and Cocoa file writers, confirmed attachment opening, clipboard, approved links, print, separate message and search windows |
| Documents | Empty archive creation, Open, recent archives and native folder/file dialogs |
| Archive services | Owner rules with revision conflicts, name/address and institution editors, persistent manual decisions |
| Import | Source selection, owner rules, scanner policy, attachment indexing, start, progress/history, stop after current message, failures and Continue Processing |
| Recovery | Read-only probe, foreground lease-protected journal recovery with elapsed time and Abort; revalidation before opening, unopened state on failure/Abort; interrupted import resume and bounded helper shutdown |
| Health | About/Preferences, scanner status and explicit background definition refresh; update availability remains platform/build dependent |

Try imports only into a disposable archive until you have reviewed the native
behavior. Source selections are read-only. Save refuses existing targets and
paths inside the archive. Reads retain the prototype's 16 MiB record bound and
256 KiB plain/raw display bound; HTML formatting is sanitized and may differ from
the original mail. These restrictions are visible errors, not dropped messages.
On macOS, the first message/file-icon drag prepares a private verified export;
the next drag offers its file URL. Multiple selected messages use a ZIP, with
at most 1000 selected IDs. This export path runs entirely in Rust, including
with no Python helper. Physical copying to Finder still needs acceptance.

## Validation and remaining acceptance

Headless targets use real services and synthetic bytes:

```sh
make test-rust-gui
make test-rust-webview
make test-rust-engine
make test-rust-recovery
make test-gui
```

The engine tests import and verify hashes, reject competing writers, edit
identities, stop an active import by closing its owner pipe, and resume it.
The browser tests exercise the shared widgets through actual Rust RPC. They
never start native windows. Native file dialogs, clipboard, printing, external
attachment opening, editor mutations and Windows job objects still need
platform trials; these tests are not native acceptance.
The recovery target creates genuine catalog/search/processing rollback journals,
checks writer exclusion, renders the foreground timer while holding the real
helper beyond 30 seconds, aborts/reaps it and verifies successful later recovery.
Preference regressions use two real window-process writers, preserve independent
stale-dialog edits and reject conflicting font changes without blocking on a lock.

Local validation on macOS (October 5, 2026): Rust build and 24 tests passed;
three Rust-backed browser tests and two archive-service tests passed. The shared
GUI (77), application/writer/loopback (40), and browser suite (34) passed;
seven native browser-suite tests were skipped. Ruff, Pylint, ty and Pyright
passed. Synthetic website screenshots were regenerated and the homepage,
Searching and Importing pages visually inspected. No native windows were run;
these results establish local validation only, pending CI and native trials.

On October 6, 2026, `make test-rust-gui-native` passed on an unlocked macOS
desktop with and without the Python helper: real staged search, message reading,
native menu capability state and About-dialog definition-refresh gating. The
synthetic archive/source byte inventories stayed unchanged, and PNGs were
visually inspected. Those initial checks did not exercise native file dialogs,
clipboard, printing, attachment launch, drag-out or embedded workflow editors.
The subsequent editor and manual trials below extend that evidence;
drag-out remains pending. The earlier locked-desktop attempt could not acknowledge paint
and is not passing evidence.
The native regression also loads owner rules, identities and import history
through the real service, with opaque-frame parent access denied. It reproduced
and repaired WKWebView's rejection of `self` script/style sources in sandboxed
editors by allowing only the page's named bundled assets. Native regressions now
drive the real HTML address-drop and whole-person merge handlers, verify their
audit records, and repeat checks after processor replay and a fresh app launch.
Physical pointer gestures remain separate from these injected native DOM events.
Manual native acceptance also verifies message exports against original RFC 5322
bytes and macOS Select All, Copy/Paste, Cut and Undo in the search and editor
fields. Native Edit actions and Open/Preferences/Quit shortcuts use the OS menu
responder chain rather than parent-page keyboard listeners.
Native printing to a local PDF was visually inspected for selected-message
headers/body/provenance; no physical printer job was submitted.
Windows native smoke queues Ctrl-comma through the real Win32 accelerator hook
and requires Preferences to open and save, with the setting retained on restart.
This test covers message-loop routing; physical-keyboard/editor-focus trials
still require separate acceptance.
The native target also exercises owner Save/Reopen and identity
Rename/Move/Separate/Reopen through sandboxed editor controls. A real
`process --reprocess` and another app process must retain those decisions while
canonical mail/source hashes remain fixed. These are explicit mutations of the
disposable fixture's settings/derived database, separate from read-only fixity.
Subsequent macOS attachment trials used a synthetic MIME message: the actual Save
dialog retained all payload bytes, Cancel returned without opening, and explicit
Open launched the identical temporary text file in TextEdit. The full archive
inventory and source hash remained fixed; closing the reader removed its temporary
copy. Rust now supplies the confirmation modal because WKWebView did not show the
shared page's JavaScript confirm. Browser regressions cover Cancel, Escape and the
headless dispatcher's continued refusal to launch even after confirmation.

The broader [issue #49 checklist](https://github.com/simsong/email-collection-toolkit/issues/49)
remains open. Specifically, this local build does **not** complete:

- Physical file dragging to Finder/Desktop, including EML and multi-message ZIP
  copies. Rust preparation and Cocoa writer checks do not establish that OS gesture.
- One application-wide window coordinator, file associations and Finder/Explorer
  document activation. Additional windows currently run separate processes.
- Windows archive-writing support, scanner/converter execution and full native
  macOS/Windows acceptance. Preserve the existing Windows writer restriction.
- Complete native upgrade/relaunch interaction acceptance. Packaging now bundles
  the private runtime and native updaters, uses the existing macOS identity,
  and publishes a shared signed feed for DMG and MSIX. Exact-candidate branch CI
  must pass installed-artifact and signing/notarization/feed gates before release.
- Full database rebuild/reprocess acceptance, large-message streaming and IMAP.

The owner authorized the Rust app as the primary alpha release while recording
physical interaction acceptance separately. This does not declare v1.0 complete
or remove the legacy Python GUI source. Local builds do not install the app.
