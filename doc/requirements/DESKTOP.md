<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Desktop requirements

Current subsystem contract and acceptance boundaries. This is not a claim of
platform/release acceptance. Deferred features live in [plans](../plans/README.md).

## Saved import recovery and notices

During archived-content and pending-ingest recovery, show the active processor,
job activity and finished-job count. Use an indeterminate progress bar while the
job total is unknown; do not display an idle-worker dashboard or finalizing ETA
for active recovery. Failed/stale saved runs shall not appear actively processing.
History status circles are at least 22 pixels, with a check for running/completed,
an X for failed/disk-full/stale, and a pause symbol for interrupted runs, alongside
text labels. Accepted GUI imports show startup immediately while archive
preparation precedes the first saved run snapshot; old failures retain their state.
Explain that finished processing jobs are work for messages or MIME parts,
potentially several per email, rather than newly imported message counts.

Ingest history Sources shows IMAP username, hostname and port instead of internal
account IDs and locator query options. Local paths remain readable. Missing or
invalid account configuration shows an unavailable-account label; viewing history
never connects to IMAP or changes saved source locators.

Opening an archive with unfinished work asks before reconnecting or processing.
Show source identity (IMAP username, server and port), first start and latest
interruption, messages imported and duplicates ignored. Sum saved attempt counts
since the preceding completion or explicit abort; these are saved progress totals,
not a fresh count from the server. If a process died without a final status, show
its last saved progress time and state that the interruption time is unknown.
Offer **Abort import**, **Ask me later**, and **Continue Import**. Later and closing the
dialog preserve work and ask again on reopening; Continue Import uses the selected
phases and saved policy. Abort cancels all displayed saved traversal and pending,
running or failed jobs under the archive writer lease, retaining archived mail,
source checkpoints and run history. A changed source history blocks a stale abort.
A later explicit import can begin a new continuation; cancelled derived work may
be regenerated with explicit reprocessing.

Search-window warnings and errors collapse after six seconds to a bottom message
pill, rather than disappearing. The pill reopens all messages received in that
window; reopening leaves the text visible until manually collapsed. Messages are
plain text in a scrollable region, including earlier warnings after another
arrives. Native application notices also remain in About during the session.

## Shutdown feedback

During active import, Quit offers **Suspend import and quit** and explains that
saved work resumes later without duplicate imports. After confirmation, loaded
application windows show a full-window translucent gray overlay (20% gray fill)
with centered black **Shutting down…** text at 50% opacity. It covers existing
dialogs, blocks content interaction and cannot be dismissed with Escape. Canceling
Quit leaves the UI usable. Display must never delay the existing five-second
shutdown deadline or interfere with deferred/canceled update installation.

## Original mailbox labels

Show IMAP folder names with percent escapes decoded exactly once, including spaces,
Unicode and brackets. Preserve literal plus signs and encoded percent signs. Decode
only display labels, after splitting the stored hierarchy: an escaped slash is part
of a name, not another folder. Local filesystem names remain literal. Stored paths,
counts, saved selections and search filtering remain unchanged in both volume modes.

## Desktop Help and user manual

macOS and Windows provide a Help menu with Community Forum, Contact Support,
User Manual, Source Code, and License. Web actions open the project's Discussions,
Issues, `/manual/` website page, and repository in the system browser. License
displays the complete embedded project GPL text offline in a selectable window.
Existing About and update actions remain available in their platform menus.
The website navigation labels its documentation entry User Manual and links to
`/manual/`; Advanced remains available through the manual, rather than the header.
The manual indexes Importing, Searching, Features (`/features/`), and Advanced.
The former `/use-cases/` route redirects to Features for existing links.
Website headings use Email Collection Toolkit consistently, and internal links
use current routes with valid section anchors. The homepage exposes the User
Manual; Curation remains an archival overview. Community Forum and Contact
Support use the same destinations as Help. Website and reference documentation
describe the supported Python desktop and preserve manual identity/tag data.
Hosted CI runs built manual/link acceptance after installing Zola; a test skipped
before that prerequisite is available does not satisfy website validation.

## Shared Python Windows desktop

macOS and Windows share the Python document controller,
search, completion, verified message reader, and HTML interface. The Windows
deliverable is an x64 MSIX, including private Python and locked runtime
dependencies; no Rust GUI or private Rust-to-Python worker is required.
MSIX assembly must retain complete dependency licenses and reject unknown license
evidence. Missing metadata labels require exact reviewed license-text evidence.
WebView2 is external and missing/incompatible runtime startup must fail visibly,
without falling back to Internet Explorer. Windows status publication must tolerate short-lived
reader sharing conflicts with bounded retries, preserve the complete temporary
file on failure, and retain a completed import status despite concurrent GUI polling.
Windows Ctrl shortcuts complement
macOS Command shortcuts. The complete GUI requires Windows archive creation,
importing, processing and identity editing, plus bundled ClamAV and working
definition updates. Reader packaging does not exclude these requirements.
Windows archive selection must use the standard system Open dialog. Selecting
any file or subdirectory inside a collection must open its enclosing archive,
including legacy archive folders without the `.mailarchive` suffix. Invalid
selections must report an error without modifying the selected file or archive.
Windows mapped drives and UNC shares must open through standard Python SQLite.
Validation, search and message reads must retain read-only database access and
SQLite locking; URI-reserved characters in archive paths must remain literal.

Reader operations must preserve every collection file, including databases.
Explicit message/attachment exports and per-user saved searches are supported.
The Python preview uses a separate package identity so it cannot upgrade or
replace the Rust preview. Signing reuses the pinned test certificate; missing
private-key access must fail, never generate an unrelated key. Versions derive
from `pyproject.toml` using the shared release mapper. Native macOS, installed
Windows activation, signing, and performance require their own evidence; a
source test or unsigned payload is insufficient to claim those gates passed.

## Desktop application documents and windows

Python/pywebview remains the selected desktop architecture. The Dioxus/Tauri
trial plan and Wry/egui migration are historical; the Rust GUI is retired.
Independent Rust importer and verification tools remain in use. End users receive
the Python GUI and dependencies together without installing a runtime.

The current pywebview application uses HTML/CSS/JavaScript and its native Python
bridge. The following asset-server rules describe that implementation and its
security baseline; they do not prescribe the unimplemented worker transport.
An application-owned HTTP server binds only to
`127.0.0.1` on an ephemeral port and serves only packaged GUI assets. Each
window starts with an unlogged, one-use cryptographic nonce that establishes a
session-only `HttpOnly`, `SameSite=Strict` cookie and redirects to a clean URL;
every asset request requires that cookie and the exact loopback host, and any
request carrying an `Origin` header must name that exact origin.
Bootstrap redirects explicitly declare an empty body. Asset HEAD responses
report the GET content length without reading or returning the asset body.
The server sends no permissive CORS response and stops with the application.
Any future state-changing HTTP API must additionally require an explicit CSRF
header. The applications must be packaged
with Python and all application dependencies. Users must not need Python,
`pip`, `pipx`, `uv`, or a source checkout.

An `ApplicationController` owns application preferences, archive-document
sessions, active-window routing, recent archives, and operating-system open
and reopen events. Native menu callbacks resolve the active window when they
are invoked. Discardable, versioned preferences are outside every archive in
the platform-appropriate per-user application directory. They contain the
last archive and at most ten recent archive paths, but no archive content or
credentials.

An `ArchiveDocument` represents one archive. Opening validates the directory
and the versioned layout and SQLite readable state of both databases read-only,
except for lease-protected recovery of hot rollback journals left by a crashed
writer. It does not create missing databases or migrate schemas. A missing or invalid saved archive is removed
from recent preferences and reported in About status and stderr; About remains hidden until requested. The document
retains the user's absolute display path and also uses a canonical,
filesystem device/inode pair as its process-local identity. Windows opened through
aliases of the same archive share the document's ingest state, child windows,
and publication generation. The document remains alive while any search
window, child window, or ingest operation uses it.

Every search window has a lifetime-stable document binding and independent
query, sort, selection, mailbox-filter, and geometry state. **New Search
Window** in the **Window** menu attaches another window to the active document;
it is disabled when no saved archive search window is active. **Open** and an
operating-system open event create a window for the requested document; they
must not silently retarget an existing search window.

Startup opens explicit document paths first, otherwise the last valid archive.
When neither is available, show a single setup window with all three numbered
boxes visible: **1. Select the root folder to ingest**, **2. Select where your
archive is stored**, and **3. Start import**. Each folder button opens a native
folder browser and displays its accepted path in a selectable, read-only text
field. Reopening a picker starts at the previous choice; cancel preserves that
choice. The archive may be an existing valid archive or a pre-created empty
directory outside the source. macOS setup pickers must disable New Folder,
including before source selection, so browsing cannot create input directories.
No archive is initialized by
selection alone. Disable Start import until both folders are selected and while
any setup dialog is pending. Reject equal or nested source/destination folders,
including symlink, Unicode, and case aliases, before creating or opening a
destination. Start
import reuses the selected root without asking for it again, retains owner email rule
setup and antivirus confirmation, and opens the Ingests progress window after
starting the worker. Cancellation of import settings keeps the setup choices available for retry.
A **Cancel** button beside Start import (also Escape) quits the application.
The Cancel action itself must not create an archive, start import, or write
preferences. It does not undo earlier writes: normal startup may already have
removed a missing or invalid remembered archive from saved preferences before
showing setup.
If another window is importing, use the normal Stop Import and Quit confirmation
and retain its writer lease until checkpoint completion or the five-second exit
deadline. It and native File → Close are disabled while a setup operation or dialog is pending.
The Close lock applies globally, including Cancel and native modal focus falling
back to an existing search window; a queued Close action must also refuse closure.
Folder pickers must clear any warning accessory left by a previous import dialog.
Cancel must atomically reserve a job-free quit against import publication; if a
job wins that race, present the normal stop confirmation and apply the shared
five-second deadline. Setup Cancel must not wait for its JavaScript reply before
requesting Quit.

When launched through `mailsearch-gui`, macOS must not reinterpret the Python
launcher or command-line option values as documents. Explicit `--archive`
handling and genuine Finder document-open events remain supported. Configure
this behavior for the running process without writing system or user defaults.

`mailsearch-gui --new` forces this setup for one launch, bypassing both remembered
and environment-selected archives without clearing preferences or altering old
archives. `--new` and `--archive` are mutually exclusive. On macOS, holding
Option (Alt) while launching the app invokes the same setup; keep it held until
the window appears. Option-clicking its running Dock icon also opens setup.
Only one setup window is shown per process. A missing or invalid last archive
is removed from recent state and reported, never recreated. Closing setup
leaves About and File New/Open available. New asks for a permanent destination
before opening a search window, then offers Import. Accepting the default
Untitled name must work. Native save results may be strings or path sequences;
neither form may truncate the path. File New/Open/Close have Command-N/O/W
shortcuts on macOS. Search windows have no Open Archive toolbar button.
The archive path appears in the native title bar, without a duplicate toolbar label.
The native menu order is Application, File, Edit, View, Window. Existing archive
directories do not require an extension. Opening checks SQLite schema and layout
read-only after any required hot-journal recovery, without scanning every database page; this is not a full corruption
audit. Open failures appear in About and stderr even if a document cannot open.

The About window is retained hidden at startup and opens through the application
menu. Closing it dismisses it
until the user chooses the application menu's About command; status updates and
Dock activation must not reopen it. Closing the last visible window keeps the
application running with About and File New/Open available. It displays the installed version, current
free space on the active archive's filesystem (or the user's home filesystem),
live Internet reachability, startup errors, warnings, and each open archive's
latest ingest status.
About populates through the real native status bridge and polls once per second.
Its script policy must permit pywebview's dynamic bridge functions, as the search
and ingest pages do. A delayed bridge retries and clears its warning on recovery.
Only explicitly selected window methods are exposed to JavaScript; application
controllers, documents, and native window objects must not be recursively exposed.

**File → New** asks for a new or empty permanent `.mailarchive` destination
before creating its blank search window. It initializes BagIt, both databases,
and operational status state, and refuses to overwrite an existing archive or
nonempty invalid directory. **File → Import…** collects one or more supported
local files or directories, owner names, explicit final
confirmation, and starts the same typed ingest service used by the CLI on a
worker thread. ClamAV has an explicit opt-out; the DMG bundles the engine and
project definitions, while development uses a local library and `etc/clamdb/`. Missing library
or configuration files produce a warning banner in the Ingests window and
macOS source picker. Scanner unavailability introduces no extra approval or
manual-review gate: import proceeds with scanner-error evidence and metadata
defects. An optional installation link opens the application release page without
installing software or starting a service. Startup errors likewise record evidence
and continue. About displays scanner and definition availability, date, age and
refresh status; explicit unscanned imports retain their warning in history.
The Ingests window provides **Import Directory…**, bound to its own archive even
when another archive is active. It opens the source picker directly, then
uses the same owner-names setup, confirmation, and writer lease as File Import.
The button is disabled during an import or while its dialogs are pending.
On macOS, one source picker accepts files and directories together with an
**Import** action, without a preliminary source-type question. Its title names
the destination archive and its message shows the full destination path.
Selecting an entire directory, including the currently displayed directory,
uses recursive discovery. Cancel dismisses the picker without starting ingest.
Final confirmation shows the Email Collection Toolkit icon and destination heading/path.
Both scanned and explicitly unscanned import confirmations use a 560-point-wide,
selectable message area so archive and source paths need less wrapping, while
retaining their existing buttons and keyboard defaults.
Every import shows two multiline fields: **Owner emails (include)** and
**Exclude (applied after include)**. Newlines or commas separate rules; blank
and comment lines are ignored. At least one include rule is required to import.
The fields default to the archive's saved `config.yaml` owner lists. Until YAML
owner settings exist, legacy `owner-names.txt` in the archive and the top level
of selected source directories may seed the fields using the new exact/glob
semantics. No checkout or launch-directory defaults are used. A saved empty
list is intentional and must not resurrect legacy defaults. Invalid settings
are reported without overwriting them.
After final confirmation and acquisition of the writer lease, changed lists
are atomically saved in archive `config.yaml` as `owner.include` and
`owner.exclude`. Canceling either dialog saves no rules and starts no ingest.
A stale dialog must not overwrite settings changed since it opened. Source
files remain untouched. The packaged source rules continue to ignore a source
root's legacy `owner-names.txt`.

The version-2 archive `config.yaml` stores owner rules and the last source-picker
directory; version-1 navigation-only files remain readable. Successful imports
remember the source directory while preserving owner lists. Unchanged settings
are not rewritten. Missing configuration starts with defaults; malformed YAML
blocks editing/import with an error, since it may contain owner policy.
This file is operational and excluded from preservation tag manifests, but
should be retained when preparing a rebuild.

**File → Document Options…** opens one window per saved archive with the same
two rule fields and a **Save** button. Saving obtains the writer lease and
checks the content revision. Import blocks edits; background status refreshes
must preserve unsaved input. Ingest records its rules in
`status/owner-rules-used.yaml`. Options compares this snapshot with current
rules and identifies older imports whose rules were not recorded.
After a completed or orderly interrupted import, atomically regenerate
`owner-names-detected.txt` as sorted, unique matching archived sender addresses,
including senders from prior imports and applying exclusions. Detected addresses
are derived evidence, never expanded into YAML or reused as implicit owner rules.
Existing Sent/Archive classifications remain unchanged by rule edits or
reindexing; correcting historical misclassification requires a fresh rebuild.
CLI processing, Continue Processing and explicit reprocessing replay their saved
ingest policy without overwriting later owner defaults in `config.yaml`.
Only an explicitly requested import saves its owner rules as document defaults.

Only a saved document holding the matching cross-process writer lease may
start ingest. The process-local document registry prevents duplicate UI jobs
but is not the operating-system lock. Different documents may ingest
independently. Successful publication increments the document generation and
identifies all attached search windows for refresh.
The OS lease is a nonblocking exclusive lock on
`status/archive-write.lock`; diagnostic JSON in that file records the operation,
PID, host, start time, version, and archive identity, but file presence never
determines ownership and a process crash releases the lock. CLI ingest and
search-index rebuild use the same lease. During Import, **File → Close** does
nothing for the owning search window and that window's close box is refused;
other search and child windows remain independently closeable. The Window menu
lists the About, search, and Ingests windows and brings a selected window forward.

`mailsearch` is a read-only command-line consumer of both databases.  It
accepts ordinary full-text terms plus `any:ADDRESS`, `from:ADDRESS`,
role-specific `to:ADDRESS`, `cc:ADDRESS`, and `bcc:ADDRESS`, `subject:TEXT`,
`date:YYYY-MM-DD`, `before:YYYY-MM-DD`, and
`after:YYYY-MM-DD` filters, intersecting every supplied term. Date selectors use the worldwide calendar-date interval described below. Results default to ten
one-line headers, prefixed by the stable `messages.message_pk`; `--limit 0`
prints all matches.  Supplying one such number prints the original RFC 5322
message bytes from canonical MBOX storage.  The current implementation finds
that message through a catalogued generation-specific MBOX location and
verifies the recovered bytes against its SHA-256 before printing. Every ingest
records the location as part of the same publication as the message catalog row.
Within one result set, message numbers are right-aligned to that set's widest
number. Interactive terminals render subjects in ANSI bold; redirected output
contains no terminal control codes.

For a numbered message, the default display shows `To`, `From`, `Cc`,
`Subject`, and `Date` headers plus all non-attachment `text/plain` parts. If
no plain-text part exists, it renders non-attachment `text/html` parts to
plain text with Beautiful Soup. `--headers` includes every header; `--html`
prints decoded HTML parts; `--mime` prints the original RFC 5322/MIME source,
including all MIME parts and attachment encodings.

`mailsearch-gui` is a macOS-first, read-only pywebview consumer of the same
search and verified-message retrieval functions.  A single search field uses
the CLI selectors and ordinary ANDed terms; shell-style quotes group spaces,
so `subject:"annual report"` is one selector while `subject:annual report`
retains the CLI meaning of a subject selector plus a free-text term. A nonempty
query follows the complete-archive count and automatic result-loading contract
above. A newer request must supersede an in-progress background search, and the
status must show its accumulated result count while it runs.

GUI query syntax errors (empty selector values, invalid dates, and unclosed
quotes) must appear as inline search feedback without a Python bridge exception.
Search pages and counts must choose filtering indexes before ordering indexes:
address selectors resolve matching distinct addresses before looking up messages;
date selectors use indexed UTC ranges under every sort order; body/attachment
terms use FTS MATCH and catalog hash lookups; mailbox selections start with
indexed source paths/volumes and observations. Preserve role, AND semantics,
complete results, and stable ordering. Literal subject substrings may scan the
subject expression index, but must fetch full message rows only for matches.
Regression tests must compile typed queries through the production parser and
SQL builders, bind their unchanged parameters to `EXPLAIN QUERY PLAN`, and
check filtering searches rather than accepting any mention of an index. Execute
the same statements to verify results and bound work on sparse large fixtures.

After three characters of the completion value (excluding its selector prefix)
and a 120-millisecond debounce, the GUI searches Any, Subject, and recognized
Date values. One Any lookup matches email substrings, original header names,
and current authoritative names, then derives From/To/Cc/Bcc choices and distinct
message counts from the matching header occurrences. It must work before content
processing finishes. Only one matching role defaults to that role; several
matching roles default to Any. Explicit selectors constrain completion to that
tag. A tile's tag menu shows the matching roles, including Any, with counts;
date tiles offer Date/Before/After. Retain existing query terms when accepting
a completion. Discard stale responses and immediately retire previous choices.
Limit individual address and subject suggestions to 20 each, alongside aggregate
substring and date choices. Address choices rank by distinct message count then
recency; roles retain original RFC header semantics. No zero-match address role
is offered. Invalid dates do not produce date choices. Removing a tile reruns
search with the remaining filters. The native window title
contains the active archive path and total deduplicated searchable-message
count.

### Worldwide date search

CLI and GUI date selectors shall cover a date anywhere across UTC+14 through
UTC−12, independent of the computer's timezone. For calendar date D, let
`start = midnight(D, UTC) − 14 hours` and
`end = midnight(D + 1 day, UTC) + 12 hours`. The interval is 50 hours:

| Selector | Required predicate on the resolved message timestamp |
| --- | --- |
| `date:D` | `start <= date_utc < end` |
| `before:D` | `date_utc < start` |
| `after:D` | `date_utc >= end` |

For `date:2020-01-05`, the interval begins at `2020-01-04T10:00:00Z`
and ends, exclusively, at `2020-01-06T12:00:00Z`. A message sent in Boston
at 10 p.m. on January 5, 2020 (`2020-01-06T03:00:00Z`) is included.
This deliberately broad interval can also include messages whose sender-local
date is January 4 or January 6. Adjacent date searches overlap by 26 hours;
their counts must not be added as if they were disjoint daily totals.
This is not a single calendar day in UTC−12: UTC−12 supplies the closing
boundary, while UTC+14 supplies the opening boundary.

Recognize ISO 8601 calendar dates (`2020-01-05`), month/day/four-digit-year
dates (`1/5/2020`), and English month-name dates (`January 5, 2020`), normalizing
them to the same calendar date. Quoted selector values support spaces, such as
`date:"January 5, 2020"`. Invalid dates must not produce date suggestions.
Compute UTC bounds once and bind indexed range predicates; do not change stored
timestamps, original message headers, date-source selection, or archive routing.
Tests must exercise both exact boundaries, all three input formats, leap days,
year rollover, the Boston example, and overlap between adjacent dates.

### Message viewing

Selecting a result shows it beside the list; double-clicking opens an
independent message window whose message pane scrolls through the complete
message, attachments, and source-location evidence. The result list can sort by date, subject, or
sender in either direction. When it has keyboard focus, Up Arrow and Down
Arrow move the selection and display the newly selected message. Result rows
show the indexed attachment count with a paperclip.
The single result column is not user-resizable. A draggable divider reallocates
width between the result list and preview without introducing a horizontal
result-list scrollbar or changing the current selection. It also supports
Left/Right Arrow (Shift for larger steps) and Home/End while focused, respects
minimum pane widths, and adapts to window resizing and the optional folder tree.
Each search window keeps its own split for its lifetime; standalone message
windows and printing have no divider. Browser acceptance tests exercise these
layout and selection invariants.
An unchecked **Search attachments** control searches only headers and message
bodies. When checked, the same ordinary full-text expression also matches the
separate indexed text-attachment table; metadata selectors retain their normal
meaning. The control does not extract attachment content on demand.
The GUI paints each result page from header metadata first, then requests its
indexed body previews on a background worker and fills a reserved third line
without blocking the initial result display. The Tabulator result table retains
complete result metadata client-side but uses its virtual DOM to paint only
visible rows; preview work is requested when a row is painted. Result rows
disable native text selection. A single selected row opens its message; modifier
clicks select multiple rows and replace the message with a selected-message count.
Dragging a selected row or the message-file icon uses the same export path: one
message becomes an `.eml`, and multiple messages become a ZIP whose entries
preserve their RFC 5322 bytes. Dragging an unselected row exports that row alone.
Message HTML links and recognized `http`, `https`, or `mailto` links in rendered
plain-text parts are never opened directly. Hovering an allowed destination
shows its complete destination in the bottom status bar. Clicking it presents
that destination with **Open Link**, **Copy Link**, and **Ignore** choices;
only **Open Link** invokes the system handler, and **Copy Link** writes the
destination as text and a URL to the pasteboard. Raw Source remains literal.
For a local file source whose stored volume-relative path begins `Users/`, the
viewer displays `/Users/...` so it remains an absolute filesystem path.
Literal, case-insensitive occurrences of every ordinary free-text query term
and every textual selector value (`any:`, `from:`, `to:`, `cc:`, `bcc:`, and
`subject:`) must be highlighted in the selected message's displayed headers and
body. Date-selector values do not create highlights.
Highlighting applies to plain text, sanitized HTML, and raw-source views without
changing canonical bytes or weakening the HTML sandbox. Its background color
comes from the strictly validated, versioned packaged `configuration.yaml`;
the initial value is yellow (`#fff59d`).
With a message selected, Command-F opens an in-message finder seeded with the
first textual archive-search term (for example, `from:beth` seeds `beth`) and
selects that term for replacement. The finder highlights its replacement text
in the displayed message; Command-G advances to the next match, and
Shift-Command-G moves to the previous match. Changing the selected message
retains the finder text but restarts at that message's first match, including
within a sanitized HTML part. The active HTML target must be visibly distinct
from ordinary archive-search highlights and scroll into view. If the finder is
closed, Command-G opens it and selects the first match.
Command-A respects the active pane: in the result list it selects every result
row, while in a plain-text or raw-source message it selects that displayed
message's text, excluding viewer headers, attachments, and provenance. HTML
keeps its native document selection behavior. A copy control copies the visible
body text; for HTML it also copies the displayed message subject and headers.

The bottom of the main GUI contains a clickable ingest-status line. During a
run it shows live completion, message count, active/configured workers, and ETA;
otherwise it summarizes the latest run. Clicking it opens a separate native
Ingests window with its own close box. The **Window → Ingests** menu opens the
same window. That window browses every retained status file and shows the
selected run's aggregate statistics, sources, failure detail, and all worker
threads. If it is already visible, either action brings it to the front and
selects the requested run instead of creating a duplicate window.

The GUI lists every non-attachment `text/plain` and `text/html` MIME part and
allows the user to select among them. When multiple HTML parts exist, it opens
the most substantive decoded part by default. HTML is isolated and sanitized. Scripts,
forms, plugins, file URLs, and remote resources are blocked by default; remote
HTTP(S) images may load only after an explicit action for that HTML MIME part. Embedded
CID images may render from the verified message. Once the sanitized local HTML
document is available, its text remains displayable while permitted remote
resources resolve; a slow remote resource must not blank or delay the part.
Attachments appear in a list,
safe images and PDFs can be previewed inline, and opening any attachment is an
explicit action with confirmation for active, unknown, or mismatched MIME/suffix
pairs. Only allowlisted matching PDF, static image, and plain-text pairs bypass
that extra confirmation.
For a non-multipart message whose complete raw body, apart from surrounding
ASCII whitespace, is enclosed by case-insensitive `<x-html>` and `</x-html>`
tags, the GUI exposes the enclosed content as a preferred **HTML — legacy
x-html** view. This recovery also applies when a malformed multipart declaration
has no usable boundaries and therefore parses as non-multipart. An inline
mention of `<x-html>` does not trigger it, valid MIME parts take precedence, and
the recovered view passes through the same sanitizer and remote-content policy
as MIME HTML. **Raw Source** remains selectable and unchanged.
At the bottom of every message view, the GUI displays the archive mailbox path
separately from every source volume and source/forensic path where the message
was found. A nonzero MBOX byte offset is displayed as `?offset=N`, rather than
as part of the pathname; an absent or zero offset is omitted. Each local source
path has a copy control that writes that path, without any offset, to the macOS
pasteboard both as plain text and as a file URL.
When a message pane is taller than its displayed content, the source-location
section remains at the bottom of that pane; it never floats immediately after a
short message body.
When `date_source` is `received-median`, the GUI shows a warning banner across
the message and gives the message well a slight red tint. The original `Date:`
header remains visible and unchanged. The banner identifies the original
`Date:` header value, the computed UTC median of the usable `Received:` header
dates, and the computed UTC date used for archive routing. The latter two values
are currently equal but are reported separately so the archival decision is
explicit.
Command-1 through Command-9 select the MIME part having that numeric part ID;
Command-0 and Command-Shift-U select the raw RFC 5322 source.

Saving a message creates a disposable `.eml` copy containing the exact
SHA-256-verified RFC 5322 bytes; it never creates or changes canonical archive
content. Message-list rows and the separate message-file icon well share the
same drag implementation, which creates that copy only when a drag starts, never
while browsing, selecting, or hovering
over a result. Because the pywebview bridge is asynchronous, the first drag
prepares the disposable file and the next drag copies the actual `.eml` file to
Finder or the Desktop. Multiple messages transfer as an actual ZIP file. Neither
operation may create a `.fileloc`/`.webloc` shortcut or expose link/text URL drag
representations supplied by the application. The application explicitly writes
only `public.file-url`, the modern file-path transfer type; macOS may add its own
compatibility aliases. Each drag export is isolated from attachment exports and later
drags; closing the viewer serializes with preparation and revokes every token.
Backends without the macOS file-drag adapter hide and reject this drag control.
The result table must finish initializing before startup clears or populates it.
Queued clicks must not select rows discarded by a subsequent search.
Message headers remain selectable text. Printing prints the displayed headers
and selected MIME part through the system print panel. Temporary message and attachment exports are removed when the GUI exits.

`summarize` is an optional macOS command that reads nonempty UTF-8 text from
standard input and prints only a one-sentence Apple Intelligence summary of at
most 30 words to standard
output. It uses Apple's on-device Foundation Models framework, never writes the
input to the archive, and reports a clear error when the device is ineligible,
Apple Intelligence is disabled, or the model is not ready.

All archive commands use `MAIL_ARCHIVE_DIR` as their default archive directory.
`--archive DIRECTORY` overrides that environment variable. If neither is set,
the command fails before reading or writing an archive.

The Python GUI identifies itself as **Email Collection Toolkit** and uses the
source-controlled rainbow-envelope icon in its native application identity.
Continuous integration runs on non-`main` repository branch pushes that change
files outside `README.md` and `doc/RELEASE_NOTES.md`, not again on its PR or
merged `main` push. Other documentation must still trigger validation. Pages
also skips `main` pushes limited to those two files. An explicit `[release-ci]`
head on any non-main branch must run DMG/MSIX/signature validation regardless
of that filter, without publishing. Signing must remove temporary private-key
material even if writing the PFX fails partway through. Forked PRs currently
need a separate CI policy. Ordinary checks and website jobs use macOS with
checksum-verified Zola; candidate validation additionally runs Windows x64.
Release publication requires a version-matching `v*` tag on main.
The required continuous-integration gate exercises the archive lifecycle and
complete HTML interface in headless Chromium with disposable fixtures. Native
Cocoa/WKWebView smoke testing is an explicit local macOS development check and
must not run in CI/CD. It uses a purpose-built one-message derived archive,
reports JavaScript-to-Python bridge completion once, exposes only `status`,
`search`, and `native_smoke_complete`, and omits the normal application menu.
It persists timestamped phases atomically and has independent child and parent
watchdogs. The first failure remains the primary report error even when
shutdown records additional failure phases. A required native-application gate
would instead need a logged-in Mac and XCUITest/XCUIAutomation.
The project also publishes a Zola-generated GitHub Pages site at
`https://simsong.github.io/email-collection-toolkit/`. The site links to the README,
release notes, GitHub releases, the current stable `v1.2.3`-shaped tag and
current beta `v1.2.3-beta1`-shaped tag when present, and project discussions.
The site uses the Email Collection Toolkit identity and shared stacked-envelope
SVG and derived PNG icons. Site validation rejects malformed Zola TOML, unreadable configuration, and
invalid UTF-8 with a path-qualified diagnostic instead of a traceback, before
checking for other missing website files.
Decorative homepage icons are hidden from assistive technology.
The home-page cover uses joined, diverging rainbow streaks, not repeating
rainbow arcs. Its title, subtitle, description, and tagline are selectable
HTML text over text-free artwork. The tagline reads "Email has a history. Keep it".
At narrow widths and browser zoom, the text reflows below the artwork and
remains available to assistive technology and when images are unavailable.
The banner's HTML dimensions match its displayed aspect ratio.
Banner sizing does not require container-query-unit support. The three desktop
tagline lines have no extra blank lines, and the mobile sentence retains
copyable spaces between words.
Provide text alternatives and readable introductory text and actions on mobile.
The horizontal keyboard photograph flows below the home-page story text at
all widths, preserves the complete image, and includes a linked Flickr credit.
Its home page gives equal prominence to individuals consolidating personal
exports and archivists curating donor collections. A separate use-cases page
describes both workflows, including an institutional digital-estate scenario,
native BagIt/Mailbag archive storage, MBOX handoff to ePADD, and explicit boundaries between
implemented and planned sources. It also provides a clearly labeled index of
digital-email-curation reports and related organizations; it does not imply
that planned application features are implemented. Every site page links to a
public privacy policy covering planned Gmail and Microsoft 365 OAuth access and
to a rights page stating the software's current GPL distribution, copyright,
and availability of non-GPL versions. The curation section renders a
responsive summary of the program's local file discovery, read-only ingest,
archive creation, search and reporting, verification, and sharing functions,
followed by its reports and organizations. Public website copy uses language
for archivists, avoids software-development jargon, and labels unavailable
functions as planned work.
The site's About page identifies Simson Garfinkel, summarizes his work with a
link to his personal website, and links to his GitHub profile and the project
repository. About links to a dated website changelog that records website
changes separately from software release notes. Website and documentation
must describe BagIt/Mailbag as native archive storage, not a separate export.
The Pages build pins its Zola release and verifies the downloaded archive
against a source-controlled SHA-256 digest before execution.

All primary navigation links remain visible at narrow widths and after
reordering; the header wraps instead of hiding positional links. Long code
examples scroll within their block without widening the mobile page. At widths
of 650 pixels or less, the page shell keeps 15-pixel side margins. Icon
regeneration closes its browser on success and failure.
Release assembly verifies the annotated tag's type, package version, and
ancestry on `main` before installing project dependencies, building artifacts,
or executing their entry points. Tag/version validation must not install the
project itself. A shared identity preflight must succeed before either the
macOS packaging job or the cross-platform Rust reader matrix starts.
Preflight must check out the triggering commit, rejecting a tag that has moved
instead of validating a different revision from the Rust build.
Pages deploys on every `main` push and after a tagged release
publishes, using the release run's signed appcast rather than a release-list
query for that new asset; the two deployment paths are serialized.

## Archive status banner

Search windows reserve footer space for ordered plain-text archive notices,
rendered with `textContent`, beside the existing ingest-history and link status.
The banner is hidden without an archive, in standalone readers, and in print.
The initial notice states that only text attachments are indexed. Tika extraction
and its indexing action/progress remain planned; no fabricated progress is shown.

## Identity picker navigation

Double-click a canonical name or address (or press Enter on its label) to open
an Any-address search in a new window bound to the picker archive. Name searches
expand original header names, canonical names and merged aliases across every
address in the current manual person group. Literal email searches remain
address-specific. Explicit group aliases survive separating a member address.
Manual merges, address moves, separation and names save immediately and survive
processor replay; automatic authoritative matching remains disabled.

Name rows show available sender-signature phone candidates, deduplicated by their
normalized number within the displayed addresses and dates. Show a single number
directly; multiple numbers use a keyboard-accessible disclosure triangle that
expands the row to list all numbers. Preserve observed spelling and do not imply
verified ownership. Institution rows do not show this column.

Click a nonzero Messages or Signatures count to open its distinct message set,
using the displayed member addresses, inclusive date bounds and evidence channel.
Email-labelled groups retain those filters and the non-person visibility choice
when their name action falls back to header evidence. Provider-domain settings
exclude those domains from institution members and institution-name search aliases;
manual institution records survive toggling the provider setting.
A filtered parent includes only its visible addresses. The resulting search has
a removable identity filter; additional terms and sorting refine that set.
No canonical message or source mailbox is changed. `make test-gui-processing`
checks grouping, search expansion, count projections, pagination and browser
navigation with purpose-made archives.

Preview workers return results without mutating window state. GUI preview requests
and polls consume completed futures, reject obsolete archive generations, surface
reader failures and allow retry. Closing prevents new preview submissions.

Application imports use retained futures. Application polling
consumes unexpected command failures into visible notices. Quit cancels queued
imports without starting processing and releases their writer leases; running
imports retain cooperative cancellation and the shared five-second deadline.
Watchdogs and shutdown cleanup never depend on available command-pool capacity.

Track Cocoa archive-open callbacks with command futures. The named future shares command admission, error collection
and bounded shutdown; native UI marshaling and source-preservation rules remain.

Track Cocoa About-window commands with futures. The named future shares command admission, error collection
and bounded shutdown; native UI marshaling and source-preservation rules remain.

Track Cocoa Setup-window commands with futures. The named future shares command admission, error collection
and bounded shutdown; native UI marshaling and source-preservation rules remain.

Track startup export cleanup with an application future. The named future shares command admission, error collection
and bounded shutdown; native UI marshaling and source-preservation rules remain.

The retained private archive adapter returns typed import outcomes to its protocol
owner. Workers do not publish shared job status; generation changes once when the
owner consumes completion, preserving errors, warnings, lease release and owner
pipe-loss cancellation. This does not restore support for the retired Rust GUI.
