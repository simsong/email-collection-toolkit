<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Desktop implementation

Current implementation and validation boundaries. Deferred feature designs
live in [plans](../plans/README.md); historical evidence does not validate this revision.

## Saved import recovery and notices

Optional processing fields in ingest snapshots default safely for old history.
`ingests.js` renders running processing activity with a striped indeterminate bar,
finished-job count, processor name and the worker's job activity. Source-file and
imported-message counters keep their original meaning; terminal/stale snapshots
never use the active-processing bar.
History uses 22-pixel circles with white check/X/pause symbols and visible state
labels, so color is supplementary. `IngestJob.started_at` lets the history bridge
expose accepted startup until a new run snapshot exists. The startup banner is
polled each second. A newly appearing run is selected even if startup finishes
between polls; further refreshes preserve manual history selection.
Historical failures are not reclassified. The finished-job card explains its unit.
The main search status line and About activity cards likewise show finished
processing jobs and the current processor instead of a source percentage/ETA.

`IngestWindowApi.history` adds typed `source_labels` for the Sources list using
`gui_processing.source_label`, shared with recovery summaries. Labels resolve
IMAP accounts from local configuration without keyring or network access; missing
or invalid configuration falls back to an unavailable-account label. Raw
`source_roots` and saved history files retain their resumable locators.

`gui_processing.unfinished_work` groups typed unfinished run snapshots by source,
stopping at a completion or explicit abort. Rootless processing does not hide
source recovery. The bridge returns nonsecret source labels, first start, latest
final/heartbeat timestamp, interruption uncertainty and saved archived/duplicate
counts. IMAP labels read configuration without keyring access or a connection.
`processing.js` shows these details before the existing explicit resume call,
triggered by the **Continue Import** button in `index.html`.
Later and Escape close the dialog without a write. The abort bridge reserves
application writer activity so Quit waits for it and rejects later writes.
`abort_processing` holds the
writer lease, rejects changed source status IDs, atomically stores typed aborted
IDs in `processing_settings` and changes outstanding jobs to the existing
`aborted` state. Status snapshots and canonical data remain intact. Explicit
message admission retries user-cancelled unpublished ingest roots; cancelled
derived jobs remain aborted until explicit reprocessing regenerates their inputs.

`app.js` retains distinct consecutive notice text in window memory, rendered with
`textContent`. After six seconds the banner becomes a keyboard-operable message
pill; hover/focus keeps it expanded. Reopening shows scrollable history and a
Collapse button without a timeout. New warnings retain previous text. Native
notices also remain in About for the application session. Bridge-start and
processing errors use the same banner. `make test-gui-processing` exercises real
archive/bridge continuation, deferral, lease conflicts, stale abort rejection,
recorded crash snapshots and browser interactions. `make test-imap` checks the
summary against a real interrupted disposable TLS IMAP download.

## Controller, search UI and message viewing

`mailarchiver.application` is the platform-neutral desktop lifecycle layer.
`ApplicationController` owns the canonical archive-document registry, logical
search windows, preferences, recent documents, startup selection, active-window
routing, and host entry points for operating-system open/reopen events.
`ArchiveDocument` owns shared per-archive ingest and child-window state and
retains the typed `WriterLease` for an active import. `WriterLease` uses
nonblocking `flock` on supported POSIX systems against
`status/archive-write.lock`. Its UTF-8 JSON is diagnostic only; the open OS lock
is authoritative and is released automatically if a process dies. CLI ingest,
GUI import, and search-index replacement enter through the same lock contract.
`SearchWindow` is a typed record for the independent query, sort, selection,
mailbox-filter, and geometry state that the JavaScript UI maintains. Preferences
are written explicitly as UTF-8 and atomically replaced in the platform
application-data directory; their randomly generated temporary filename has no
user-derived component. They are treated as optional, discardable state.

`application_menu` adds the shared Help destinations using the system browser
adapter, with Windows' existing About/update actions retained. `show_license`
reuses a selectable local window containing escaped, complete GPL text, without
a JavaScript bridge. Both desktop packagers copy the root `LICENSE` into their
GUI resources; source runs use the root file. `make test-gui` verifies callback
dispatch and offline license content. The Zola `/manual/` index links to the four
guides; Features uses `/features/` with a `/use-cases/` redirect. The shared header
links User Manual and omits Advanced. `make test-website` exercises actual Zola
output and `make test-website-navigation` checks responsive header layout.
The homepage uses the shared product name and current Features routes, including
its audience anchors. Its resource card links to the manual index; curation
resources are labeled reports and organizations. Built-site tests validate all
same-site links and their HTML fragment targets. The detailed repository
reference remains linked separately from the website manual index.
`make website-download-check` runs the built manual/link acceptance alongside
installer rendering after CI provisions Zola, so the earlier missing-tool skip
cannot leave those navigation checks unexecuted.

`PyWebViewApplication` adapts that model to native pywebview windows without
putting platform imports in the controller. Its File menu asks for a new or
empty destination and initializes it before creating a blank window, opens an
archive in a new search window, runs Import, and closes the active window unless that window owns the
running Import. Window close events enforce the same rule. The Window menu
routes to the active archive's shared Ingests child window and enumerates every
About, search, and Ingests window. **Window → New Search Window** creates another
view of the active archive; the macOS adapter disables it without an active
saved archive search window and refreshes on native focus changes.
A narrow macOS adapter refreshes pywebview's
process menu when window or ingest state changes and disables the Close item
when the controller would refuse it. `handle_open_documents` and
`reopen` are stable host entry points for the extension/activation work in
issue 76. Windows shell work and frozen application/installers remain owned by
issues 73, 72, 74, and 75 respectively.

The CLI and GUI construct the same Pydantic `IngestRequest` and call
`run_ingest()`. The GUI acquires and publishes the per-document writer lease,
runs the service on a non-daemon worker thread, leaves readers usable, polls the
existing typed status files in every attached search window and the persistent
About window, and invalidates view caches after the run. Startup and import
errors are retained as typed notices instead of being available only on stderr.
Before creating NSApplication, `configure_macos_application` registers the
process-local `NSTreatUnknownArgumentsAsOpen` default with string value `NO`.
This prevents Cocoa from reopening the source launcher and option values as
documents; argparse and the Finder delegate retain their existing roles.
`make self-test-gui` launches the actual `mailsearch-gui` entry point using the
same isolated diagnostics as the packaged app and rejects unexpected startup
errors. The native application probe also checks startup notices and opens its
fixture through the document-open delegate.
The Ingests bridge exposes **Import Directory…** for its bound document. It
reuses that document's search window (or opens one to own the job) and runs the
normal confirmed import workflow.
The UI polls import availability and prevents repeated clicks while dialogs are open.
Import's final confirmation uses an application-owned macOS NSAlert with the
bundled icon, destination heading/path, and explicit Import/Cancel actions.
The shared alert helper marshals presentation onto the Cocoa main thread and
supplies the import confirmation icon.
An application-owned NSOpenPanel labels source selection **Import**, displays
the destination, and enables both file and directory selection, including
multiple selections. Both import entry points open this panel directly without
a source-type question; directories use recursive discovery.
`owner_rules.OwnerRules` validates and normalizes typed include/exclude lists.
Each glob matches the entire mailbox component; `@` explicitly introduces a
separate domain glob. Includes are evaluated first, then exclusions override.
Case is ignored; no display-name, substring, or automatic plus-address expansion
occurs. A bare exact rule also matches the same legacy sender without `@`.
`_run_ingest` resolves rules before creating the catalog and uses this matcher
for Sent routing after the infected-message check. CLI `--owner-names-file`
optionally supplies include rules while retaining configured exclusions;
without it the archive YAML supplies both lists.

`DocumentOptions` loads `config.yaml` defaults, falling back to legacy archive
and selected-source-root owner files only when no YAML owner settings exist.
Every import presents native include/exclude text areas on macOS and an
`options.html` prompt on the portable host. Both accept newline/comma-separated
rules, validate before continuing, and save nothing on Cancel. Final confirmation
passes typed rules to `start_import`, which obtains WriterLease, checks the
revision captured before the dialog, and saves changed lists atomically.
Sources and legacy owner files are never rewritten.

The document-bound `DocumentOptionsApi` uses the same lists in an options child
window. Save checks the lease and revision; status polling disables edits during
ingest without clobbering unsaved text. The import snapshot is
`status/owner-rules-used.yaml`, allowing change warnings across restarts.
After workers stop and canonical checkpoint publication completes, a streaming catalog
query regenerates `owner-names-detected.txt` from all matching archived senders,
including previous imports, with excludes applied. Addresses are sorted and
unique; unsafe line delimiters are omitted. The file is derived evidence, not
configuration. Neither this file nor owner configuration is in preservation
manifests. Tests in `test_owner_rules.py` exercise real fixture import, stored
categories, raw hashes, saved defaults, exclusion precedence, idempotence and
standalone archive verification; GUI service and native application tests cover
both fields, cancellation, revision conflicts and persistence.

`archive_config.py` writes version 2 YAML containing `owner.include`,
`owner.exclude`, and `last_import_directory`, while reading version 1 as well.
Unchanged lists are not rewritten. Successful import updates navigation only
when needed and preserves the rules. Malformed configuration raises an error
instead of discarding potentially important owner settings. The source picker
uses the saved directory if it exists, otherwise the archive parent.
Existing message categories remain unchanged by settings edits or FTS rebuilds.

When startup produces a placeholder, the shell discards it without creating a
native search window and opens `setup.html` with all three numbered steps.
`SetupApi` exposes only the two folder pickers, Start import, and Cancel through
`WindowBridge`; picker cancellation retains its server-side selection. The
source and destination NSOpenPanels accept only existing directories and disable
New Folder, even before a source is selected. This prevents browsing from
creating a directory inside an input tree before overlap validation. Folder-only panels treat `.mailarchive` packages as
directories so existing archives remain selectable. `SetupSelection` compares folder and ancestor filesystem identities for overlap
(including Cocoa Unicode and case aliases) before
any archive initialization. Start import opens a valid existing destination or
creates an empty one, reuses its search owner on retry, and passes the chosen
root into the existing confirmed import workflow. A started job opens Ingests
and hides setup after clearing its selections. The webview stays alive to
receive the bridge reply; destroying it inside that call would strand a reply
thread at exit. Cancel (also Escape) requests Quit on a background thread without
waiting for that bridge reply. `request_quit` first calls
`prepare_quit` under the same application lock used to publish import jobs: a
job-free decision sets `_quitting` before new jobs can register; otherwise it
confirms active ingest, stops jobs, and applies the bounded exit policy below. An opt-in native regression
publishes a real leased job immediately before that decision and verifies the
confirmation, stop signal, lease retention, completion, and application exit.
Picker selection and the Cancel action do not persist setup paths or write
preferences. Normal startup may already have removed an invalid or missing
remembered archive through `_forget_recent()` before showing setup; Cancel does
not undo that cleanup. Start import calls `open_document()` or `create_document()`,
which records the destination in application preferences before import settings
are confirmed. After ingestion, the worker may save the selected source directory
in the archive configuration through `remember_import_directory()`.
Pending setup operations
disable Cancel and native File → Close and prevent window closure. Menu state
refreshes on every setup lock acquisition and release, including Cancel and error
recovery. The native Close gate checks the setup lock independently of the active
or fallback window, and the Close handler rejects queued actions while locked.
Native regressions inspect both Cancel transitions and a real modal picker over
an existing search window with no webview key window. Shared
NSOpenPanel instances clear their accessory view before setting a new warning. Errors stay
visible and controls recover for retry.
`make test-startup` covers launch precedence, preference preservation, folder
identity checks, and the three-box layout at default/minimum window sizes.
`make test-native-setup` selects disposable folders through the actual Cocoa
panels and bridge, checks cancellation and inline validation, imports one real
message, verifies its SHA-256 and untouched source, and requires clean exit.
`ApplicationController.startup(new=True)` bypasses explicit and remembered paths
without changing preferences. The GUI maps `--new` and macOS's current
`NSEvent.modifierFlags()` Option flag to that path before normal startup.
The Dock reopen delegate samples the same flag and restores or creates setup.
The native setup target also tests the sampler before application configuration,
explicit/remembered archive bypass, normal Dock reopen, and Option reopening one
existing setup window. Only the global hardware-modifier source is substituted
with real NSEvent flags to avoid sending keystrokes to the user's desktop; this
does not establish a physical Option-click/Finder launch test. Physical
Option-launch and Dock Option-click remain unverified.
The user holds Option through launch because the flag reports current key state.
About is created hidden to retain the event loop and File New/Open after setup
closes; the application menu explicitly shows it. Native dialog
paths normalize SAVE strings and OPEN/FOLDER sequences before indexing; New
validates the destination inside its error handler. Cocoa File menu items carry
explicit Command-N/O/W shortcuts. Archive opening uses the File menu rather than
a toolbar button.
The search toolbar omits the archive path; the native title bar identifies it.
The Cocoa adapter reorders File, Edit, View, Window after the application menu.
It resolves Cocoa focus on the main thread and still builds menus from the
logical active document or About when a background app has no native key window.
Closing About hides its retained native window instead of recreating it, keeping
the event loop alive even when no document is open. The hidden window is omitted
from the Window menu. The application menu's About command restores it; Dock
activation does not. Quit permits its actual destruction. The native lifecycle
probe exercises dismissal, background updates, and explicit menu reopening.
`WindowBridge` restricts pywebview introspection to explicit callable names,
excluding public controller and window object graphs. About allows the same
dynamic bridge generation as the other pages, retries bridge readiness, polls
status each second, and clears stale errors after successful refresh.
Archive Open validates schema metadata without `PRAGMA quick_check`, which
previously scanned multi-gigabyte databases before opening any window. Existing
extensionless directories remain accepted. `make check-archive-open ARCHIVE=...`
performs this read-only compatibility check without launching windows or saving
preferences. This check does not certify every database page against corruption.

`make test-native-application` runs the production About and search bridges with
a disposable extensionless archive, checks rendered notices and native menu order
and Open shortcut, and closes the application. It runs only on a logged-in Mac.

`LoopbackAssetServer` owns GUI delivery. Bootstrap redirects send
`Content-Length: 0`; HEAD obtains the asset length from filesystem metadata
without reading its contents, while GET sends the asset bytes. It binds `127.0.0.1:0`, issues a
different one-use bootstrap ticket for every new window, sets a random
session-cookie name and value, redirects away from the ticket, and serves only
resolved files below `gui/`. Direct unauthenticated requests, ticket replay,
foreign `Origin`, an incorrect `Host`, and traversal fail closed. It emits no
request log and no CORS allowance. WKWebView/WebView2 service calls continue to
use pywebview's native JavaScript bridge; there is currently no HTTP API.

The `gui/` interface uses pywebview's Cocoa/WKWebView backend on macOS.  Its
Python API delegates query parsing, SQLite reads, and direct MBOX retrieval to
the same typed functions used by `mailsearch`. A nonempty query directly asks
the shared catalog/FTS predicate for up to 2,000 ordered headers. This avoids
placing an exact or threshold count on the interactive path; SQLite FTS5 has no
reliable approximate cardinality for arbitrary combined full-text/filter
predicates. A larger result set immediately shows that prefix and
then requests the unlimited remainder from offset 2,000 through a second
bridge promise using the same SQL predicate and stable sort, so the two phases
are complementary and cannot change membership or ordering. The result status
is red and says **Searching in background** until
the remainder arrives, at which point the materialized length supplies the
exact count. Search-generation checks discard a stale response when a newer
query starts. Preview requests are split into
100-message batches on one promise queue, and queued work from an older search
generation is skipped. An empty query clears the result list without invoking
SQLite and clones a static help template containing full-text, phrase, and all
selector forms into the result pane. The interface contains no manual
result-pagination controls. The UI is conventional: a search toolbar above a
result list and message pane. Independent
message windows load the same static application with a message-number parameter;
their message pane fills the window and scrolls independently so source-location
evidence remains reachable.
The headless regression explicitly makes the message taller than its viewport,
then scrolls to and checks the visibility of source locations; it does not rely
on font-dependent fixture height or iframe load timing to create overflow.
The main window polls the latest shared `IngestStatus` once per second and
renders it in a bottom status line. The separate `ingests.html` application
polls all typed status files and presents run history beside aggregate and
per-worker detail. Both the status-line action and the native
**Window → Ingests** menu route through a per-document singleton window owner: an existing
window is restored and ordered to the front, while a closed one is recreated
with its own normal close box. A running file whose heartbeat is older than
five seconds is displayed as stale without rewriting its retained JSON.

The native shell loads the checked-in 192-pixel PNG derived from
`gui/icons/rainbow-post.svg` before falling back to a system symbol, so the
Python application and its About/Dock identity use a stable project asset. The
`website/` directory is a Zola site using the local
`envelope-rainbow` theme. GitHub Pages builds it from `main`; the workflow
SHA-256 verifies the pinned Zola archive before extraction, resolves the newest
published stable, alpha and beta assets into Zola data, then deploys a Pages artifact. The
homepage renders separate Windows and macOS download buttons as static links.
Published release metadata supplies exact URLs; stable installers are preferred,
with preview installers offered before a stable release exists. Both buttons
remain visible on every platform, without JavaScript or browser detection.
Absent assets use the generic releases URL with a static availability notice.
“Show all installers” stays alongside both buttons. A preview-only primary
release has a visible testing warning; Windows certificate instructions appear
beside the buttons as well as in the additional-links disclosure.
The website has no update-stream control or browser-local update preference.
Both app Preferences panels use shared stream labels from `updates.py` and
persist the existing `release`/`preview` channel through the document controller.
Sparkle's `allowedChannelsForUpdater_` reads that channel when checking;
WinSparkle's gateway supplies it to the authenticated feed filter. Preview adds
published alpha/beta/development builds while retaining stable updates.
`make website-download-check` runs rendered Zola and real HTTP/browser checks
for both static platform buttons, missing assets, scripting enabled/disabled and responsive layout.
The home-page template uses a light rainbow design with capability and story cards
and equal individual and archivist columns. The cover displays the text-free
`images/cover-artwork.png` derived from the approved banner. CSS fits the
central artwork into the original wide banner proportions, excluding the
image generator's blank top and bottom margins. The image's HTML dimensions
match the displayed 1312:287 ratio rather than the source file's dimensions.
Its title, subtitle,
description, and "Email has a history. Keep it" tagline are semantic HTML
positioned over the artwork on desktop and in normal flow below it at widths
of 1000 pixels or less. The decorative image has empty alternative text;
the visible heading and paragraphs provide the accessible content. Banner text
uses capped viewport-relative sizes without container-query units. Literal
spaces between tagline spans are retained for selection and mobile flow;
Chromium checks verify exactly three desktop lines without whitespace gaps.
The story card stacks its text above the user-supplied `images/hands-typing.jpg`
(799 × 372). The photograph scales proportionally without cropping and has a
caption linking to Image Catalog on Flickr and its stated CC0 dedication.
`make website-preview` reuses its disposable checkout-root output directory
with Zola `--force` and runs a temporary preview on loopback port 1111
(overridable with `WEBSITE_PREVIEW_PORT`) without publishing. The Zola configuration retains the
existing project URLs under the Email Collection Toolkit title. The checker
parses its TOML before the generic required-file check and reports file-read, UTF-8 decoding,
and TOML syntax failures without a traceback; `make website-icons` regenerates PNGs
from the shared SVG using Chromium, closing the browser even on rendering or
write failure. Navigation wraps at every width; long
documentation code blocks scroll within the page. The `use-cases.md` content
page supplies the detailed personal-archive and donor
digital-estate narratives. It describes BagIt/Mailbag as native archive storage and standard
MBOX as the ePADD handoff, with planned direct-provider, first-class package
import, and automated interoperability work labeled explicitly.
The base template links every page to `privacy.md` and `rights.md`. The privacy
page describes planned Gmail and Microsoft 365 OAuth data access, local
storage, read-only use, revocation, and provider-policy commitments without
presenting the reserved adapters as implemented. The rights page records the
current GPL distribution, copyright, and possible non-GPL availability.
The reusable `section.html` template renders section content and child-page
cards through the site theme. The curation section adds a responsive five-part
summary of local file discovery, read-only ingest, archive creation, search and
reporting, verification, and sharing. Public copy describes implemented and
planned functions in language intended for archivists.
The primary navigation links to `about.md`, which describes Simson Garfinkel
and links to his personal website, GitHub profile, and project repository.
About links to `changelog.md`, a dated record of website changes distinct from
application release notes. The 2026-09-07 entry records the storage-format
wording correction and the new About/changelog pages.
The release workflow requires a version-matching annotated tag, builds a source
distribution, writes `SHA256SUMS`, and publishes a GitHub Release.

Result ordering is a server-side SQL whitelist over date, case-folded subject,
or case-folded sender with a stable message-number tie break. The Tabulator
result table owns focus, rendering, and row components, while the application
maps Up/Down to selection and message display.
The sole Tabulator column has `resizable: false`. A focusable vertical separator
uses pointer capture and keyboard controls to adjust a CSS grid track. Its
per-window fraction is clamped to 300-pixel list and 320-pixel preview minima
(half the available width when smaller). Resize observers account for the
folder tree and window dimensions and remeasure HTML previews; Tabulator's own
container observer refits its column without replacing data or selection.
The separator is hidden in standalone and print layouts. Headless browser tests
drag the real divider and check widths, overflow, selection, and keyboard limits.
The older bounded-recent optimization remains internal to the command-line
client, whose automatic exact fallback preserves its one-call behavior. The
GUI always invokes the complete SHA-256/FTS query because an archivist may be
looking for any period in the collection. It materializes the first ordered
page without a count probe; first and background queries use the same stable
sort and complementary offsets, so the combined set has no skips or duplicates.
Subject and Sender modes sort the
complete matching set rather than using recency to choose page membership.
Result paperclips use attachment counts joined from the disposable search
metadata without rereading MBOX content. Each row reserves a third line;
Tabulator 6.5.2 is vendored under `gui/vendor/tabulator/` (MIT) so the desktop
application remains offline-capable. Tabulator's virtual DOM paints only its
viewport and buffer while retaining the complete result data. Its formatter
queues preview IDs only when it paints a row through the Python bridge. A
single-worker executor reads the indexed 18-word previews, and JavaScript polls
the typed result batch until it can update visible rows. The result-table boundary
cancels native `selectstart`, and rows use `user-select: none` so file drags do
not select card text. Tabulator handles modifier-click selection and virtual
scrolling without custom pointer-range code. A single selected row displays its
message. Multiple selected rows replace the message with a count and the same
file well. An explicit drag from either source prepares the selected export;
clicking a row remains a normal message click. Global macOS Command-key handlers
select numeric MIME part IDs or raw source. Command-F opens an in-message
finder from the first current search-highlight term and selects its input;
Command-G opens the finder at its first match when it is closed, while
Shift-Command-G and subsequent Command-G presses cycle its matches. The finder adds a distinct
current-match outline without changing canonical bytes or the archive-search
predicate. Each accepted HTML render carries a viewer-generated per-render
marker; cross-frame navigation keeps direct references only to marks bearing
that marker, so email-provided IDs and CSS classes cannot redirect it. The
current mark receives an inline, important orange style and scrolls immediately,
after a short timer fallback, and after two animation frames. Image/font load,
error, and window-resize events remeasure the iframe and retain its active
target in the outer message pane. Finder updates are debounced and re-render the
selected part with any existing part-local remote-content authorization. Finder,
selection, part, and frame-identity generations discard stale responses and
stale key continuations. The viewer waits for the parsed local iframe document,
not every remote resource, so authorized slow images cannot blank local message
text. The sandbox grants same-origin access only to this inert, scriptless
sanitized document; scripts remain prohibited and the document keeps its
restrictive CSP. It does not grant popups: a frame-level link handler writes an
allowed destination to the bottom status bar on hover, prevents its default
click, and presents **Open Link**, **Copy Link**, and **Ignore**. The native
bridge independently validates only `http`, `https`, and `mailto` destinations;
it writes copied links as both text and a URL, and opens them through NSWorkspace
only after **Open Link**. Plain-text rendering tokenizes only those same allowed
URL schemes into links and installs the identical capture-phase handler; Raw
Source remains literal text. Selecting another message resets the finder index to its first
match without replacing the finder text.
Command-A uses the most recently clicked pane: it marks all result rows as
selected in the list, or creates a DOM range over the displayed body for plain
and raw text, excluding viewer headers, attachments, and provenance. Text
inputs retain their normal native select-all behavior. The toolbar copy control
passes visible plain/raw body text to the native pasteboard; for HTML it adds
the viewer subject and headers to the rendered document's visible text.
The toolbar's **Search attachments** checkbox passes an explicit boolean to the
typed search service. Ordinary terms search `message_fts` by default; when the
box is selected they search the union of `message_fts` and `attachment_fts`.
Metadata selectors are unchanged.

Each typed GUI search page also returns its deduplicated ordinary free-text and
textual-selector values; date selectors remain filters only. JavaScript marks
literal case-insensitive matches when it renders header, plain-text, or
raw-source text nodes. For sanitized HTML it parses the already
inert document, marks body text nodes, injects only the configured mark style,
and then supplies the result to the existing sandboxed iframe. The versioned
packaged `configuration.yaml` is loaded into strict Pydantic models and exposes
the initial `#fff59d` highlight background through the GUI status response;
the color accepts only six-digit hexadecimal syntax before it reaches CSS.

Search completion starts after three characters, waits 120 milliseconds after
the latest keystroke, caps each address and subject group at 20 entries, and
discards responses superseded by newer input. Address results rank by
deduplicated message count and then last-seen date.

The optional original-mailbox explorer is built from
`source_files.hierarchy_path`; volume-relative `source_path` remains the exact
physical provenance. Pydantic node identifiers encode a normalized logical
path and, only in explicit-volume mode, the stable source-volume identity;
browser input never supplies SQL. MBOX files, structurally recognized Maildir
roots, Apple Mail `.mbox` package chains, and directories containing only
direct EML/EMLX files become logical-mailbox leaves. Exact node counts use
`COUNT(DISTINCT observations.message_pk)` with the path/volume and
`observations_source_file_offset` indexes. Hidden-volume trees merge identical
volume-relative paths. IMAP-only branches use `urllib.parse.unquote` once for
labels after hierarchy splitting; stored path/token keys and SQL counts stay
escaped. Local and mixed-source branch labels remain literal. `make test-gui`
checks both volume modes, exact selection filters and literal/Unicode names;
`make test-gui-processing` checks the rendered Gmail tree and checkbox filtering.
The two tree modes are cached for the active archive.

Selected branches become one correlated `EXISTS` predicate inside the
materialized candidate query, before its `LIMIT` and `OFFSET`. The predicate
uses `observations_message_pk`, so multiple source observations provide union
semantics without duplicating a canonical result. Hiding the explorer sends no
selection while retaining its browser state. Versioned Pydantic filter sets
are fsynced to a temporary file and atomically replaced in the platform's
per-user preferences directory; the archive is never written. Python
`FilterSetStore` acquires the persistent `.lock`
companion before loading for save/rename/delete and hold it through replacement.
Unix uses `flock`; Windows locks byte zero with `msvcrt.locking`. Closing releases ownership; the lock file is never removed.
Read-only listing reads a complete published JSON snapshot. Real Python subprocess tests hold the shared lock, publish an intervening baseline,
then verify that save, rename and delete retain all expected changes and leave
archive bytes unchanged.

MIME descriptions and API responses are Pydantic models.  Body content is
loaded only for the selected part.  HTML parsing removes active elements,
event handlers, file URLs, and unsafe URL schemes, replaces image CID references
with message-local data, and injects a restrictive CSP.  The HTML is displayed
inside a sandboxed iframe. Remote image URLs are omitted unless the user
explicitly enables them for that view. Reserved part IDs identify raw RFC 5322
source (`-1`) and a synthesized legacy x-html view (`-2`). Before enumerating
parts, the viewer tests a non-multipart message's raw body against an anchored,
case-insensitive `<x-html>...</x-html>` wrapper. This includes a malformed
multipart declaration that produced no parsed children, but excludes valid
multipart messages and bodies that merely mention the tag. The enclosed bytes
are decoded through the archive's best-effort text decoder, sanitized by the
same HTML path, and derived again from verified raw bytes when selected; the
canonical message is never rewritten. Individual image and PDF attachments
are base64-transferred only on an explicit preview action; other attachment
payloads are written to a private temporary directory before macOS opens them.
The viewer also reads the archive mailbox location and linked source
observations from the catalog, then displays archive path, source-volume label,
and source or forensic path at the bottom without treating an archive mailbox
as a source. It renders nonzero byte offsets as `?offset=N` and omits zero or
absent offsets. A local file source stored as `Users/...` on the root volume is
displayed as `/Users/...`. A local source-path control copies only the mounted pathname to
the macOS pasteboard, as both text and a file URL; provider and forensic paths
without a local pathname have no copy control. Direct provider observations sort before local evidence; retained
Apple Gmail observations are labeled as local cache copies rather than as the
authoritative cloud source.
The message-content and message-well containers are vertical flex layouts; the
source-location section uses an automatic top margin so it occupies the bottom
of any spare viewer height without affecting normal scrolling for long messages.

For a catalog row whose `date_source` is `received-median`, the typed message
response includes the decoded original `Date:` header, the stored UTC
`date_utc` as the Received-header median, and that same UTC value as the archive
routing date. The UI renders all three values in its warning banner. Keeping the
median and routing fields distinct makes the current routing decision explicit
without requiring the viewer to recompute archival date policy from headers.

`search.sqlite3` contains separate `message_fts` and `attachment_fts` virtual
tables so message text remains searchable without attachment matches.
`message_metadata`, keyed by message SHA-256, contains an
indexed mapping to the message and optional attachment FTS row IDs, an
attachment count, and deterministic 18-word body preview. FTS updates and
publication recovery resolve SHA-256 in this ordinary table and delete virtual
table rows by row ID, avoiding a full FTS scan. The `message_attachments` table
is keyed by SHA-256 and attachment ordinal, with the MIME-walk part ID, decoded
filename, and normalized MIME type.
Indexing parses each message once for FTS body text and attachment metadata;
`--index-attachments` additionally writes decoded text attachments to
`attachment_fts`.
The tables are derived and are replaced together with FTS by `refresh-index`.

`.eml` export writes the bytes returned by hash-verified direct retrieval.
Finder dragging uses an `NSPasteboardItem` with exactly one explicitly supplied
type, `public.file-url`, containing the exported path as a file URI. Cocoa may
add compatibility aliases, including `NSFilenamesPboardType`; the application
does not add URL-link or text types to the native writer. JavaScript carries only
an opaque registered export token with a copy-only operation mask; the Cocoa
adapter replaces that token before the native drag starts. Cocoa process setup
installs it before either normal document or native smoke windows are created.
The legacy
`dragImage:...` path clears WebKit link flavors and writes the file object; the
modern `beginDraggingSessionWithItems:...` path replaces the item writer before
AppKit builds its pasteboard. Unregistered text and link drags are unchanged.
Every preparation writes into a fresh `drags/<uuid>/` subdirectory, isolating it
from attachment basenames and later exports. Preparation and close share a lock;
close revokes its tokens and rejects later preparation before cleaning exports.
The status bridge disables the drag control on non-macOS backends. Startup
awaits Tabulator `tableBuilt` before clearing the initial result viewport. Queued
row-click callbacks verify the search generation and current row before selecting.
The browser acceptance test rejects unhandled page errors and verifies export bytes.
`make test-file-drag` checks exact
export bytes, token revocation, both native pasteboard representations, and
injected selector/superclass dispatch on a controlled AppKit host.
Result cards and the message-file icon well both call `installDrag`; selected
rows use the complete selection and an unselected row uses only its own message.
Modifier clicks select multiple rows; pointer drags export files. Message headers
remain normal selectable text. The browser never preloads an
`.eml` file on hover or selection: a drag-start event begins asynchronous
preparation, and a subsequent drag transfers the ready file. Each write uses a
unique same-directory temporary pathname before atomic replacement.
`window.print()` is handled by pywebview's WKWebView
print operation. Temporary exports live only for the application process.

The shell page permits `unsafe-eval` only for local scripts because pywebview
constructs its typed Python API wrappers with JavaScript `Function`. Message
HTML remains isolated in a sandboxed frame with its own restrictive CSP. The
`test-native-gui` Makefile target opens a hidden smoke-only page over Cocoa. The
page calls `status()` and one real `search()` through the injected bridge, then
reports exactly one result to Python instead of making Python synchronously
poll WKWebView JavaScript. A dedicated bridge exposes only those three methods,
and smoke mode supplies no custom application menu. The child atomically
records timestamped phases and requests shutdown from a watchdog; the pytest
parent has a separate timeout, captures a macOS process sample, and terminates
the process group if needed. Later shutdown failures add phases without
replacing the original bridge error.

`aisummarize.py` implements the `summarize` console entry point. It reads stdin
before doing any native work and invokes a content-addressed Swift helper built
into `~/Library/Caches/mailarchiver` from the packaged `apple_summary.swift`
source. The helper uses `SystemLanguageModel.default`, checks model
availability, and asks for one faithful abstractive sentence of at most 30 words while
treating input text as untrusted content rather than instructions. Neither the
input nor output is canonical archive data.

Use `uv` for dependencies and every test/run target through the repository
Makefile.  Typed Pydantic structures carry all message metadata and external
API responses; dictionaries are confined to API-boundary decoding.

## Theory of operation: Python desktop and ingest

The default application is Python/pywebview. Its application controller manages
windows/documents, and its GUI services handle search, completion and reading.
Import calls the existing Python ingest engine to preserve original message
bytes, write MBOX and manifests, update databases/indexes and handle recovery.
The installed package supplies Python and dependencies; no user-started service
or external interpreter is required. Independent Rust executables provide import
and verification tools where configured.

### Desktop history

The supported desktop and ingest engine are Python. Retired Rust GUI architecture,
search experiments and acceptance evidence are in [RUST_GUI_EXPERIMENTS.md](../RUST_GUI_EXPERIMENTS.md).

## Shared Python Windows desktop

The Windows implementation was developed separately from main and selectively
reuses `work-rust-gui`'s deferred POSIX import, indexed attachment lookup,
organization-name matching, saved-filter locking, and Windows MSIX tooling.
`gui_app.py` remains the common host. `desktop_platform.py` adapts Windows
clipboard, explicit file/URL opening, and WebView2 preflight. Windows starts with
an archive-opening welcome window and reader menus; macOS retains its existing
setup and menus. Ctrl and Command both invoke the reader's selection/find actions.
Windows uses pywebview standard system Open file dialog. The common document
controller resolves selected files/subdirectories to the nearest enclosing
`.mailarchive` folder or folder containing an archive database, then performs
normal database validation. A damaged nearer archive cannot fall through to an
outer archive. Recent documents record the archive root, not the selected member.
`sqlite_paths.sqlite_uri` preserves escaped filenames and places UNC servers in
the URI path with an empty authority. This avoids the bundled SQLite rejection
of network URI authorities after mapped-drive canonicalization. The GUI uses
this helper for validation, search, completion, provenance and processing;
readers use `mode=ro` and recovery/editing explicitly request `mode=rw`.
`make check-archive-open ARCHIVE=...` validates member selections and reads one
message through both display and canonical paths, without ingestion or recovery.
New/Import, processing and identity controls now use the shared Python services.
Windows leases use pinned non-reparse directory handles and LockFileEx; Windows
publication flushes files and uses write-through renames with bounded retries for
Windows sharing violations. Status writes use the same helper so GUI polling
cannot permanently disable progress updates after a transient reader conflict.
The stdlib MBOX subclass
recognizes LF/CRLF separators independently of the host and writes framed bytes
without stdlib newline translation. Temporary input files close before reopening.
The initial Windows payload omitted ClamAV. Resource discovery now checks the
private runtime, branch-local portable runtime and conventional Windows install.
The updater supervisor uses a kill-on-close Windows job instead of POSIX groups.
The MSIX builder now requires the native runtime and baseline definitions.
Its runtime-license gate recognizes clr_loader's omitted metadata label only
when the shipped full MIT text matches the reviewed SHA-256; unknown texts fail.
Windows scanner startup uses an isolated interpreter that loads libclamav before
application imports, avoiding Python's older same-named OpenSSL DLL. An inherited
owner handle terminates the worker on parent exit; anonymous pipes retain typed
requests and concurrent native scans. Version probing also runs in isolation.
Small health-check samples use ClamAV's memory mapping API; normal archive input
continues through file scans. Actual Windows clean/EICAR, concurrent verdicts,
input preservation and startup-deadline checks pass through `make test-windows-scanner`.
`make clamav-seed SEED_DIRECTORY=...` copies and validates a local baseline into
`%LOCALAPPDATA%/Email Collection Toolkit/clamav/generations`. First-run refresh
can bootstrap without bundled definitions. FreshClam diagnostics and CDN cooldown
state survive failures; failed validation leaves the active generation unchanged.

The MSIX contains x64 CPython and runtime-only locked dependencies, with an
isolated `python312._pth`. Manifest activation runs `mailarchiver.desktop_entry`
through private `pythonw.exe`; there is no Rust GUI executable. Shared GUI assets
are inside the private runtime. `ECT.PythonReader` is distinct from `ECT.LocalTest`.
The existing pinned certificate signer and bundle/install checks are adapted to
x64. The Windows WinSparkle adapter shares update state/preferences and writer
reservation with macOS Sparkle. A private tokenized loopback gateway validates
complete feed signatures and selects only the Python x64 package identity.
The SDK handles installer verification and confirmation; the app schedules daily
checks in installed builds. Source launches permit manual checks only and refuse
installer execution. Help contains Check for Updates and Update Settings.
Actual SDK initialization and signed-feed filtering pass focused tests; an
end-to-end signed Python package upgrade remains unvalidated. The published feed
does not yet contain a Python Windows release. Native file drag remains unimplemented.
Windows definition download remains blocked by a CDN cooldown during local
acceptance; native scanning and validated local initialization pass. macOS
acceptance remains unvalidated.
macOS packaging remains the Python builder on main.

`reader_fixture.py` constructs only a newly reserved synthetic fixture, without
enabling the POSIX archive publisher on Windows. Make targets exercise shared
search/render/export, byte preservation, native promise bridging, and relocated
MSIX execution. [PYTHON_WEBVIEW.md](../PYTHON_WEBVIEW.md) tracks actual validation
and outstanding native/signing gates; earlier Rust packaging evidence is not
evidence for this Python payload.

## Windows development setup

[WINDOWS.md](../WINDOWS.md) describes building and running the x64 Python/WebView2
desktop and MSIX. The retired Rust/MSVC and Dioxus notes are preserved in the retrospective. Windows now uses native writer locks and the embedded scanner; see
[Shared Python Windows desktop](#shared-python-windows-desktop).

## Matcher appearance, completion and Quit

Matcher rows use 2pt vertical cell padding and compact disclosure controls, with
black matrix text and darker supporting labels. `scheduleSuggestions` dismisses
stale choices immediately. `search_selectors.py` defines typed selector entries
with recognizer/normalizer, tag, label, family, model field, and parameterized
SQL builder. The parser and search predicates dispatch through that registry.
`search_completion.py` recognizes the active selector, preserves preceding terms,
and begins work only after three value characters. A materialized Any query
resolves names/addresses once and groups observed header roles, distinct message
counts, and recency. It returns bounded address choices plus aggregate roles;
Subject and recognized Date values use the shared predicates. Read-only attached
identity data supplies current canonical names and recorded aliases; a temporary
name view avoids persistent schema changes or rebuilding content indexes.
Generic GUI tiles consume typed tags and choices, including dates. Headless tests
exercise tag menus, explicit selectors, compound terms, and date normalization.

GUI jobs distinguish ingest from content-only processing. Quit confirms only
active ingest, then signals every job and gives active workers at most five
seconds before forced process exit. Idle Quit exits immediately. The Cocoa
delegate returns Cancel during the grace period so callbacks can still run.
Final import refreshes are suppressed during shutdown. GUI SIGINT requests the
same bounded stop without a dialog.
Headless regressions cancel a real cooperative processor and verify retained
pending work; deferred-but-unstarted jobs never become active merely on quit.

## Cooperative shutdown and writer fencing

The Cocoa termination delegate confirms active ingest with **Suspend import and
quit** and requests bounded Quit. After confirmation and watchdog arming, a
best-effort background dispatcher injects `gui/shutdown.js` into loaded webviews
through a bounded thread pool. The shared script closes existing and late-arriving dialogs and
shows an idempotent full-window modal with 20% gray fill and centered 50%-opaque
black Shutting down text; Escape cannot dismiss it. A shared external
`shutdown.css` clears inherited modal backdrops before opening the overlay. Overlay replies are outside
the shutdown completion budget. Deferred/installing Sparkle-owned termination
skips the overlay, retaining usable windows if installation is canceled.
`make test-gui-processing` verifies rendered styling and blocked interaction;
`make test-native-quit` checks delivery to real Cocoa About and Ingest windows.

`IngestJob.stop` signals all document jobs. The common local/native and API source
loop checks cancellation before requesting another message. A fetch already in
flight can finish; `ProductionPipeline.ingest` temporarily defers cooperative
cancellation through that message's ingest/message pipeline while keeping processor
deadlines. Discovery, scanner startup and content-only processing remain cancellable.
Workers attempt the existing interrupted-run checkpoint and lease-release path.
`resume_ingest` replays one durable message at a time with the same cancellation
deferral; the runtime filters by message identity so another pending message
cannot start before the next stop check.

With no jobs, live import workers, tracked writes or export cleanup,
`request_quit` calls `os._exit(0)` immediately.
Otherwise a daemon waiter gives all jobs, worker tails, tracked writes
and non-UI private-export cleanup one monotonic
`QUIT_TIMEOUT_SECONDS` budget of five seconds, then calls `os._exit(0)` even if
they remain blocked; it exits sooner when workers finish. It does not destroy
windows, wait for UI callbacks, run atexit handlers or enter Python finalization.
The native event loop remains available during the grace period. Sparkle's
deferred/installing phases retain ownership of termination and relaunch.
Setup Cancel and GUI SIGINT use the same path; CLI Ctrl-C remains graceful.
Document-option and identity bridges reserve `writer_activity` under the same Quit lock;
both setup and File New also reserve it around archive initialization and registration.
Explicit message/attachment saves reserve it after the destination dialog through
atomic replacement; standalone message bridges retain the same application owner.
Filter-set save/rename/delete operations also reserve it through preferences replacement.
New writes are rejected after Quit reservation. Export directories carry their owner
PID. A later launch removes only private directories owned by this user whose
PID no longer exists; live or inaccessible owners are preserved. The scanner
helper watches its multiprocessing parent sentinel on a daemon thread and uses
immediate exit on parent death, even during engine startup or a blocked scan.
Import startup reserves writer activity through lease/settings I/O and job
registration, releasing a failed startup's lease before its completion event.
Definition refreshes reserve the same activity. FreshClam runs in a spawned
supervisor's private POSIX process group; the supervisor watches the GUI parent
sentinel and kills the entire group on parent death or command completion.
The independent Quit watchdog starts before stop/snapshot lock acquisition.
`prepare_quit` and `has_active_ingest` use nonblocking application/controller/document
job probes. Unknown state requires confirmation and cannot reserve an idle Quit.
Canceled confirmation never arms the watchdog, and canceled Sparkle installation
invalidates that Quit's event. Regression gates pause real archive initialization
between databases and real exports before replacement; subprocess probes hold each
of the three locks while requesting Quit.
Setup Cancel dispatches its Quit thread before either locked/unlocked menu refresh,
so a stuck refresh cannot prevent the watchdog from starting.

Forced exit may leave an unfinished transaction, append or BagIt checkpoint.
GUI Open first validates read-only. If SQLite reports a hot rollback journal,
it acquires the writer lease, rejects database/sidecar symlinks and hardlinks,
opens the existing database read-write for rollback, and repeats validation.
This also checks and recovers the optional `processing.sqlite3`, without creating
or migrating it, before the GUI reads pending work or the saved resume request.
Processing validation requires the complete V2 table set. New processing databases
are initialized transactionally in a private sibling file and renamed into place
only after closing the complete schema; interruption before publication can leave
an unused `.processing-init-*` file, while the optional database remains absent.
The production writer lease covers this initialization. Existing partial schemas
are rejected without silently creating missing tables.
The next ingest uses SQLite rollback and `recover_publication` to truncate an
uncatalogued append or retain a committed message, then refreshes manifests.
The incomplete-work prompt or File → Import resumes saved work; this is recovery,
not a guarantee that every interrupted archive immediately passes verification.
Production processing prepares interrupted jobs once when opening its pipeline;
per-message replay uses the dispatch loop without recomputing aggregate reports.
Explicit status/report requests still calculate complete archive statistics.
`make test-quit` exercises actual process termination, multi-job stop, rollback,
partial-append recovery, native-source and loopback-HTTP message boundaries, and
duplicate-free restart. `make test-native-quit` tests the real Cocoa About window
during periodic status polling. Its existing `MAILARCHIVER_NATIVE_GUI_E2E` opt-in
flag requires a macOS GUI session. These tests use only disposable archives.

## Archive status banner

Search windows reserve footer space for ordered plain-text archive notices,
rendered with `textContent`, beside the existing ingest-history and link status.
The banner is hidden without an archive, in standalone readers, and in print.
The initial notice states that only text attachments are indexed. Tika extraction
and its indexing action/progress remain planned; no fabricated progress is shown.

## Identity picker search navigation

Native Close callbacks from pywebview's worker threads dispatch Cocoa focus lookup
and closure to the main thread, with a bounded result wait. This includes personal
default-list editors that are not attached to an archive document.

`IdentityPickerApi.search` validates a typed selection against `picker_page`,
opens a search session for the picker archive and passes startup query parameters.
Name labels produce shell-quoted Any selectors; unnamed groups labeled with an
email address use a full-group header scope to distinguish them from individual
address searches. Counts produce `IdentityMessages`
with the visible address IDs, channel, inclusive dates and display label.
Email-label fallbacks copy the request with only the action changed, preserving
visibility and date constraints. Institution membership and name aliases filter
`organization_domains.provider=0`; durable organization records remain intact.
`gui/app.js` runs the startup search and displays a removable scope chip; search
page and count bridges apply the same scope. The read-only SQL joins processing
evidence to catalog messages by SHA-256, preserving distinct messages and excluding
the plain-text fast path for scoped searches. `make test-native-application` exercises real Cocoa picker navigation while another
archive is open; Chromium exercises filtered counts, the removable scope chip
and the shipped widgets through real bridges.

`PhoneNumber` observations are projected onto address rows and unioned for each
visible person group. The Name table renders one observed number directly or a
native HTML `details` disclosure for multiple numbers. Chromium and native Cocoa
fixtures exercise expansion alongside name/count navigation and manual grouping.

Preview requests retain each future with typed archive/generation/message metadata.
`request_previews` and `take_previews` alone merge completed results under the
preview lock; invalidation cancels queued obsolete work. Running obsolete results
are consumed without publishing or clearing newer pending IDs. `make test-gui`
uses real SQLite locking, archive replacement and missing-index failures to verify
these boundaries without replacing the preview reader.

An eight-worker application executor retains named import futures. Status/notice
polls consume completed results and errors. Import futures remain tracked through
worker tails; cancellation of a queued import closes its controller job and lease.
Quit stops admission, cancels queued work and waits on futures under the existing
deadline. The independent watchdog, export cleanup and quit waiter remain outside
this pool. `make test-quit` covers pool saturation, queued import cancellation,
visible command failure, and unchanged recovery and process-exit bounds.

Cocoa Open, About and Setup commands and startup export cleanup share named
futures, command admission, error collection and bounded shutdown. Native UI
marshaling and source-preservation rules remain. Dedicated-thread exceptions
are documented in the [threading audit](../THREADING.md).
