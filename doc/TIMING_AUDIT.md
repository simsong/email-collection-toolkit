<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Timing and synchronization audit

This audit covers active application, importer, helper, build and test code.
Baseline: `5334c6a041a77d26994332973da9f4abdff1f082`; working branch:
`codex/signed-installer-labels`; existing PR #195. The local candidate advances
from alpha 57 through alpha 58 to **1.0.0a59**. It is not a published release.
The user's final policy replaces **all first-party non-timeout timers**,
including scheduling, debounce, sampling and retry delays.

## Original failure and diagnosis

The diagnostic fixture let a short-lived storage worker finish before expecting
its stack in a periodically collected sample. A sampling interval could never
establish that the sampler observed that worker. This was a test race; it did not
demonstrate lost email or a production ingestion race.

Specifically, the baseline requested a 20 ms sampling interval and slept for
80 ms in the storage worker. Four nominal intervals do not guarantee the
collector is scheduled while that worker exists. Its duration assertions also
measured the deliberately inserted sleep rather than successful collection.
The construction substituted elapsed time for a collector acknowledgement;
that inference was invalid on either operating system.

The repair holds the real fsync/SQLite worker on an Event until a newly requested
sample is fully published. A condition acknowledges publication; metadata records
sample start, finish, thread count and errors. Elapsed time is not collection
acknowledgement. Separate production and collector defects found during the wider
search are listed below.

## Scope and method

The working-tree inventory includes 381 owned code/configuration files across
Python, stubs, JavaScript, Rust, PowerShell, C#, shell, workflows, Make, HTML,
AppleScript, Swift and YAML. Python syntax traversal counted 3,059 function and
method definitions. These are inventory counts, not proof of every possible
interleaving. Vendored JavaScript and the archived experimental Rust UI were
excluded; supported Rust importers, verifier, downloader and build tools were included.

Searches covered direct calls and generated source strings: sleeps, timer APIs,
periodic waits, native run-loop delays, retry backoff, polling, wall/monotonic
clocks, discarded wait results, joins, subprocess ownership, futures, locks,
barriers, Events, request generations and asynchronous replies/consumers.
Callers and substantive tests were inspected beyond matching lines. External
libraries and operating systems retain their own internal scheduling.

The initial lexical inventory returned 149 timer-related lines in 56 files.
Those are search hits, not 149 bugs: legitimate failure timeouts and measurement
code were included. The repair groups below distinguish the demonstrated races,
cleanup defects and deliberate removal of previously intentional scheduling.
Changing multiple tests does not establish that each one had independently failed.

## Repairs

| Area | Defect or timing dependency | Replacement |
| --- | --- | --- |
| Diagnostic collection | A worker could disappear between samples. | Hold the real worker until an explicitly requested subsequent sample is published. Sampling follows phase/request events. |
| Diagnostic publication | Report I/O held the profiler-data lock; publication errors could hide the source failure. | Separate data/publisher locks, copied snapshots, acknowledged errors and original exception retention. Obstruction tests use the real publisher lock. |
| Native watchdog fixture | File-size polling guessed when output appeared. | Acknowledge actual watchdog pipe output after collector cleanup. A prior native dump/fstat hang remains recorded; its native cause is unproven. |
| Status reads | Slow About/Ingests/options requests could overlap or reuse pre-command state. | One owned Promise, dirty-state draining and a fresh read after older work completes. Release ownership in the same continuation that finishes draining. |
| Desktop refresh | Periodic status/bridge polling. | Actual filesystem/task/focus and bridge-ready notifications; only readiness failure deadlines remain. |
| Suggestions/previews | Timed polling collected background results. | Blocking replies wake on completion, cancellation or generation changes, with failure deadlines. Reject stale results. |
| Typing/find | Debounce and zero-delay scheduling timers. | Immediate cancellable requests and owned microtasks for same-turn batching; retain selection/frame generations. |
| Notices | A timer automatically collapsed messages. | Keep expanded until Collapse or Dismiss; retain UUID deduplication and window scope. |
| Progress | Refresh/throttle ticks kept owners and status files active. | Queue/cancellation notifications participate in future waits. Coalesce identical non-temporal status state and recognize live producer PIDs. |
| IMAP CLI | Periodic reporting revisited committed totals. | Native archive/status/WAL notifications and joined final reporting. |
| Scanner startup/commands | Callback polling revisited cancellation during IPC. | Actual result futures and propagated activity/cancellation notifications; reap owned processes/readers on failure. |
| Scanner delivery | Short IPC polling loop. | Dedicated reception, queued requests and future completion notifications; one reply publisher. |
| Scanner teardown | A second closer could release a detached worker's live runtime. | Serialize the entire stop/join/runtime cleanup; a real acknowledged-termination worker verifies concurrent ownership. |
| Processor waits | Sleep loops checked file gates/cancellation. | Cooperative `wait_for_files` and `wait_for_cancellation`, with native/host notifications and invocation failure deadlines. |
| Windows console | Python 3.12 Windows lock waits cannot rely on signal interruption. | A retained native callback schedules the existing Python handler and wakes owner waits. GUI stop Events remain separate; Windows execution is outstanding. |
| PST/libpff | Short timed loops checked activity/exit. | Native output changes and actual child exit. Renew PST inactivity only on size changes; preserve limits and receipts. |
| File registration | Discovering targets before registering parents could miss a new WAL or intermediate directory. | Register each parent before discovering/opening targets; rediscover newly appeared parents. Deterministic Mac regressions retain real handles and kevents. |
| Windows replacement | Backoff hoped that readers would release handles. | Metadata readers permit read/write/delete sharing. Compatible replacement succeeds while open; external denial fails explicitly and preserves files. |
| Native acceptance | Delay/focus assumptions hid modal-entry and activation races. | Actual modal-loop observations, focus events, action-Promise joins and menu execution acknowledgements. |
| Native replies | Pinned Cocoa/WebView2 SDK reply semaphores could survive webview destruction; a faulted WebView2 continuation could strand a reply. | Per-window futures settle once on completion, error, Close or a final failure deadline. Preserve SDK serialization/Promise results; queue evaluations asynchronously and reject UI-thread waits. Cocoa lifecycle passed; native Windows acceptance remains outstanding. |
| Bridge teardown | A thread snapshot was treated as an admission fence. | Join the owning window's callbacks and stop producers before teardown. State the snapshot's actual limit. |
| Windows drag | Delays guessed mouse-down, drag start and target entry. | Await actual source/target acknowledgements, retaining copy-only and byte checks. Windows execution remains required. |
| Updates | Periodic native/Python checks and deferred-install retry timers. | One enabled automatic check on installed application launch; native periodic scheduling disabled. Activity changes revisit deferred installs; manual checks remain. |
| Research downloads | One-second Blob URL revocation guessed consumption. | Document/browser owns URL lifetime; retain actual download assertions. |
| Rust downloader | Timed interruption, accept, release and split-header loops. | Tokio notification, blocking accept with explicit shutdown wakeup, channels/conditions and first-line consumption acknowledgement. |
| Rust PST progress | One-second throttle suppressed actual work notifications. | Report each actual tick/checkpoint; a real two-tick test retains identical mail output. |
| Rust subprocess tests | Repeated timed `try_wait`. | Blocking child wait and a channel failure deadline; kill the owned group and verify reaping. Keep piped input open so `Child::wait` cannot change the fixture into EOF. |
| Signing/feed helpers | Delays after failed commands. | Finite attempts inspect actual results and fail after exhaustion; no delay implies external readiness. |
| ABBYY research helper | Busy polling without a completion event. | Its installed scripting dictionary exposes no completion event. Reject an asynchronous busy reply, use an owned input copy and retain working files/error evidence if external work remains active. Native OCR is unvalidated. |
| Shutdown | Joins/completion flags could conceal surviving workers or cleanup errors. | Check liveness, retain live resources, attempt independent cleanup and aggregate failures. Forced/incomplete Quit exits 1; completed Quit exits 0. |
| Recovery fixtures | Observing the first row still required the parent to beat all remaining messages. | A real processor holds the next message at an acknowledged stop/release boundary; retain partial publication, exact restart/deduplication and source-byte checks. |
| IMAP interruption fixture | The parent could race all downloads or inspect SQLite before committed startup. | Gate actual TLS body requests, require committed status, acknowledge Ctrl-C before releasing held requests, and assert exact resume with/without real scanning. |
| Update notifications | A Pydantic private default lambda became a bound descriptor, breaking state-change callbacks and cleanup. | Optional instance-owned callback; regression verifies changes reach the assigned observer without leaking across model instances. |
| Headless browser fixtures | Removed polling/collapse timers had been supplying unacknowledged refreshes and overwriting recorded states. | Join real job completion, deliver the native event explicitly at the headless transport boundary, hold/reorder actual replies, and collapse notices through their real button. Native transport remains separately tested. |
| Collection-only completion | An unselected row's completed drag export changed a Map without a DOM mutation or state notification. | Replace the cache Map on completion/invalidation to publish the state assignment; the actual reader/export driver retains per-message and copy-only checks. |

## What remains timed

Only failure limits remain in first-party scheduling sites: socket/HTTP, subprocess,
IPC, invocation, worker/cleanup, native/DOM/bridge readiness and watchdog deadlines.
Expiry is not success. A documented inactivity deadline may be renewed by actual
output activity, never by a tick.

Browser paint callbacks and microtasks are execution/render notifications, not
elapsed-time timers. UTC timestamps, certificate validity, definition age and
elapsed/CPU measurements are data. Dependencies retain internal scheduling.
Finite retries remain; without a completion event they can exhaust before an
external service becomes ready rather than silently guessing readiness.
Diagnostic stacks are now captured at phase boundaries and explicit requests.
They no longer automatically resample a silent hang during a phase; this is an
observability tradeoff of removing periodic sampling, not proof that hangs vanished.

## Observable changes

- Suggestions start immediately after the existing three-character threshold;
  superseded requests are cancelled or rejected by generation.
- Warning/error banners remain expanded until explicitly collapsed or dismissed.
- Status/elapsed/rate displays follow work, filesystem, task and focus events.
  Blocked work does not fabricate heartbeats.
- Enabled automatic program checks run when the installed application opens;
  manual checks remain available and no recurring check is scheduled.
- Compatible Windows metadata readers no longer obstruct replacement. An
  incompatible external reader causes explicit failure with preserved files.
- ABBYY's research integration rejects an unacknowledged asynchronous export and
  retains its private working directory rather than deleting an active export.

## Validation and evidence boundaries

The final local checks below are separate from native/package acceptance. Logs,
including earlier failures, are retained under `.tmp/timing-audit/no-timers/`.
The table above contains 35 repair/change groups, not 35 independently reproduced
production races.

- **Ordered static checks passed:** Ruff, Pylint, ty and Pyright, including the
  converter project (`static-28.log`). No rules were suppressed.
- **Full Python suite passed:** 1,401 tests, 19 skips, 493.61 seconds
  (`make-check-3.log`). The skips were 13 Windows-specific cases, three isolated
  research cases, two native Quit cases and one isolated-runner Keychain case.
  Converter prerequisite: 18 passed. Later changes were confined to browser
  fixtures/transport and the JavaScript drag-cache notification.
- **Rust checks passed:** workspace formatting, Clippy and tests; the real embedded
  scanner test stage also passed (`make-check-3.log`). The final PST progress
  regression reports two actual ticks without changing mail output.
- **Full browser stage passed:** 102 tests, 10 native opt-in skips, 69.95 seconds;
  both real corpus/lifecycle imports also passed (`browser-final-6.log`). The
  focused notification/TLS pause-resume batch passed all 14 selected cases first
  (`browser-focused-4.log`).
- **Interruption/reply regressions passed:** both real TLS Ctrl-C/resume modes,
  including real scanning, and three future ownership races passed
  (`interrupt-replies-3.log`, five tests; converter prerequisite: 18 passed).
- **Website checks passed:** synthetic search/import images were regenerated
  (`website-screenshots-final.log`); source/assets validation and Zola build passed
  (`website-build-final-2.log`). The rendered homepage, Searching and Importing
  pages were captured and visually inspected (`website-site-final-2.log` and
  `.tmp/website-previews/`). The Searching instructions now describe manual
  collapse. These are browser captures, not native frame/version evidence.
- **Native Mac evidence has a revision boundary:** real setup import/Cancel/race
  passed three tests (`native-setup-2.log`); reply/filesystem tests plus the actual
  native lifecycle passed nine (`native-lifecycle-5.log`). These preceded the
  final shared reply-registry and drag-cache edits. Final focus/menu/setup/HTML-find
  acceptance could not be rerun after the desktop locked again; it is pending,
  not implied by the browser results.

The single aggregate `make check` was not reported as green: after its Python/Rust
stages passed, the browser stage exposed remaining fixture assumptions. Its owned
run was stopped and verified terminated; logs and the interrupt stack were kept.
After the fixture repairs, a full browser rerun exposed one missing collection
notification. That source repair passed the full rerun above. Final stages were
completed separately without rerunning the unchanged eight-minute Python suite.
Final `make ruff` and `git diff --check` also passed (`ruff-final.log`).

No current-head GitHub CI, signed package, installer, appcast or live release
acceptance is claimed. The checks above were local. Results for a subsequently
published commit must be verified separately; no merge, tag or release is authorized.

## Remaining limits and acceptance

1. **Windows VM acceptance is required.** Native notifications/sharing, console
   interruption, OLE, WinSparkle, C#/PowerShell waits and source/relocated-package
   behavior cannot be established by Mac tests. Use existing Make targets and
   retain actual frame/menu/version evidence; edited source is not acceptance.
   The Linux inotify path also requires Linux execution; this Mac run exercises
   kqueue, not the other kernels' notification implementations.
2. A live PID is not a stored process-start identity; PID reuse can misclassify
   legacy running snapshots. External process death without a final write is
   discovered on the next status read, not a periodic tick.
3. File events mean recheck state, not successful publication. Watch the WAL for
   SQLite writes and require committed results or producer acknowledgement.
   Remote-volume notification guarantees remain platform-dependent.
4. Cooperative in-process plugins can still ignore cancellation or block in native
   code. Hard interruption needs a separately scoped process boundary.
5. A callback snapshot is not an RPC admission fence. Native reply ownership now
   contains closed-window callbacks; it does not prove every third-party lifecycle
   or preceding synchronous cleanup call is bounded.
6. External signing/notarization/CDN and ABBYY behavior need their own real checks.
   Removing backoff does not make an unavailable external service ready.
   A deferred Cocoa update blocked only by an unrelated CLI's installation guard
   is revisited on application activity/focus, not that CLI's silent lock release.
   Refocus a toolkit window after the CLI finishes. Cross-process release
   notification is still missing; the exclusion/safety guard remains enforced.
7. Event-driven progress changes I/O frequency and profiles add overhead. This
   audit proves no speedup, original Windows 600-message cause, antivirus/disk
   attribution or root cause of the earlier native crash.
8. **Independent review is not cleared.** Three completion-review rounds were
   exhausted before the final scanner repair and strict timer-policy changes.
   No fourth round was started; explicit restart is needed for further independent
   review. The November 8 commit-and-push request authorizes publication to the
   existing draft PR without claiming review clearance. Tests do not substitute
   for that review.

## Handoff

Canonical alpha 59 and its lockfile are prepared for publication to draft PR #195.
The unrelated `macroscope_evaluation` remains untouched and excluded. Implementation,
local checks, native/Windows acceptance and independent review are separate states.

For local Windows VM acceptance, use the existing Make targets serially against
their purpose-made fixtures: ordered `make ruff pylint ty pyright`, then
`make test-ci-diagnostics`, `make test-progress`, `make test-ingest-progress-service`,
`make test-python-writer`, `make test-pff`, `make test-gui-processing` and
`make test-python-reader-native`. Include the real file-sharing regressions in
`tests/test_file_events.py`, `tests/test_portable_writer.py` and
`tests/test_standalone_verify.py` through `make test PYTEST_ARGS='...'`.
These commands are a remaining acceptance plan, not Windows results. Keep
source and relocated-package evidence distinct and retain native screenshots.
