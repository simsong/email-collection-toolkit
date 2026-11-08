<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Timing and synchronization audit

This audit covers active application, importer, helper, build and test code.
Baseline: `5334c6a041a77d26994332973da9f4abdff1f082`; working branch:
`codex/signed-installer-labels`; existing PR #195. Alpha 59 was published as
commit `2d28c4b00ba4aa4edc3e5c11da07f547027a8230`; alpha 60 followed in
`99b6cdbae94d9550574fceb76f23d5fb5e91612e`. The current reported-CI repair is
**1.0.0a61**. These statements do not establish release acceptance. Sections after
the alpha 61 repair retain the earlier audit's recorded revision boundaries.
The user's final policy replaces **all first-party non-timeout timers**,
including scheduling, debounce, sampling and retry delays.

## Alpha 61 — reported CI and deadline repair

Baseline for this new reported failure repair is published alpha 60 commit
`99b6cdbae94d9550574fceb76f23d5fb5e91612e`, tested by
[CI run 42515468486](https://github.com/simsong/email-collection-toolkit/actions/runs/42515468486).
Its Python assertion relays the JavaScript driver's `passed=false` result and
`Timed out: risky attachment requires confirmation` message. This is not itself
proof that the application failed to request confirmation.

The driver's confirmation callback increments a private counter without changing
the DOM or publishing an event; its waiter observes DOM mutations and explicit
state events. The retained CI trace contains no counter value. A local full-driver
baseline run passed, demonstrating that the failure is schedule-sensitive. A
quiet-page diagnostic using the shipped waiter and real browser confirm callback
recorded **one callback, zero DOM mutations, timeout** without publication; with
explicit publication it completed. The driver now publishes its confirmation,
clipboard and captured-link changes and includes the observed confirmation count
in any future failure. Attachment safety/confirmation policy is unchanged.

A separate real-file regression reproduced Macroscope's deadline finding before
repair: a false predicate publishing file activity caused a second predicate call
after a zero deadline, because the ready native wait succeeded. The helper now
rejects expired remaining time before waiting. The same pattern was found in
suggestion and preview waits sharing a notification signal; both failed a bounded
busy-notification regression before repair. Their loops now enforce expiry while
preserving typed pending/error state and explicit retry.

Ordered Ruff/Pylint/ty/Pyright passed, followed by 171 affected unit tests and
18 converter tests. The complete browser suite passed 105 tests with 10 explicit
native opt-in skips, plus both end-to-end imports. A fresh Sol High completion
review cleared this entire new 13-file diff in round 1/3. Evidence is retained in
`.tmp/ci-425154-repair/`, including original CI logs/trace, baseline failures and
the quiet-page negative control. This new repair's local
review does not clear the earlier observer-lock repair's independent-review gap or
establish Windows/Linux native acceptance. No CI monitor or release job is started.

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

The alpha 59 inventory included 381 owned code/configuration files across
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

## Macroscope review follow-up — local alpha 60

The forwarded comments are from Macroscope, not Copilot. Eight findings refer to
published commit `2d28c4b`; its ordinary CI passed while release-candidate packaging
was skipped. Those checks missed the interleavings/volumes below and do not cover
this uncommitted follow-up. The fixes preserve the user policy: no timed progress
throttle and no successful polling tick on a result-wait timeout.

| Review thread | Assessment and local repair |
| --- | --- |
| [Retained find barrier](https://github.com/simsong/email-collection-toolkit/pull/195#discussion_r4506376732) | Valid schedule-dependent fixture defect. First join a real retained render, then explicitly start a wrapped render, bound barrier entry and join all old consumer Promises even on failure. |
| [Windows update completion](https://github.com/simsong/email-collection-toolkit/pull/195#discussion_r4506376733) | Valid partial-publication race. Capture `UpdateService.check_finished` before SDK discovery; completion follows the timestamp, phase and detail, including error state. |
| [PST diagnostic volume](https://github.com/simsong/email-collection-toolkit/pull/195#discussion_r4506376735) | Valid output-limit regression. A new eight-byte private counter changes at every work checkpoint; progress history emits only phase changes and power-of-two counts. At most 64 count-triggered records are needed; error diagnostics retain their independent limit. No elapsed-time throttle is restored. |
| [Sparkle probe exception](https://github.com/simsong/email-collection-toolkit/pull/195#discussion_r4506376744) | Valid probe failure. Catch only the exact expected synthetic exception, then verify error recovery, cleared reservation and exactly-once continuation consumption. |
| [Large result arrays](https://github.com/simsong/email-collection-toolkit/pull/195#discussion_r4506376753) | Valid JavaScript argument-limit defect. Append with `concat`; preserve request-generation checks. The new widget test admits 150,000 constructed rows beyond a real result and scrolls to the last row. It does not claim a 150,001-message archive or bounded total result memory. |
| [Linux descriptor limits](https://github.com/simsong/email-collection-toolkit/pull/195#discussion_r4506376766) | Valid API limit in both inotify and pidfd waits. A shared scalable selector replaces `select()`. The real high-descriptor pipe regression is portable; Linux kernel-specific acceptance remains separate from Mac results. |
| [Importer initialization cleanup](https://github.com/simsong/email-collection-toolkit/pull/195#discussion_r4506376768) | Valid in both libpff and PST supervision. Native activity initialization now occurs inside the already-started child's kill/reap `finally`. Failed observer-thread creation also closes real subscriptions. Fault injection is confined to unavailable native-resource admission; child execution and cleanup remain real. |
| [Result-wait timeout state](https://github.com/simsong/email-collection-toolkit/pull/195#discussion_r4506376771) | The lost structured response is repaired, while timeout remains an explicit failure as required. Return typed error plus actual pending state; an explicit retry may read later completion. Silently returning normal pending state every 30 seconds would recreate a timer-driven polling cadence. |

The first 32 focused regressions passed, including real PST imports with and
without scanning. Rust workspace checks and all 14 real scanner/importer tests
passed. Both selected browser cases passed: the actual retained-find driver and
the 150,001-row widget test, which scrolls to and displays its last row. Ten native
Mac cases passed on the unlocked desktop: lifecycle, HTML find, four setup cases,
two Preferences cases and two Quit cases. The full aggregate subsequently passed;
the broader counts and final-repair evidence boundary appear below. The user explicitly restarted independent review and authorized publication on
November 8. Restarted round 1 found the six additional defects below; the changed
candidate requires another review. Earlier exhausted rounds remain recorded.
These repairs are not yet independently cleared or published.

## Restarted completion review — alpha 60

The fresh Sol High reviewer examined the complete 145-file task patch, not only
the eight-thread follow-up. Round 1 reported six findings; all were accepted.

| Finding | Repair and regression evidence |
| --- | --- |
| Silent PST/OST waits missed cancellation | Pass run-owned `ChangeEvent` through plug-in context to both converter owners/stages; wake native waits and kill/reap. Three regressions use acknowledged silent children, including the full ingest path, with failure-only containment deadlines. |
| Suggestions ignored pending/error replies | Handle error before completion in the actual browser consumer; a blocked real request shows failure after one wait, then an explicit new input retrieves completion. |
| Close veto warning had no wakeup | Drain queued scoped notices off the native thread without the occupied command executor. A contended-lock/all-workers-busy regression requires publication without any status read. |
| Released-document watches accumulated | Reconcile watches with controller ownership; close outside the registry lock and remove ownership only after success. Real repeated reopen/close tests preserve child ownership, then require watcher exit. Also release native subscriptions if observer thread admission fails. |
| Preferences probe retained a 300 ms pump | Check actual native visibility, use Cocoa display capture, and retain only failure deadlines. |
| About disk failure generated refresh notices | Return typed `disk_error` and display unavailable health observationally. Real disconnected-fixture browser reads must not issue new notices. |

Focused validation passed nine Python cases and two browser cases, plus both
end-to-end import prerequisites. Ordered lint/type checks passed after correcting
a precise unsupported typed native method; no rule was suppressed. The two native
Preferences cases and full native lifecycle also passed after these repairs
(`restart-r1-native.log`). The broader aggregate then passed as recorded below.

Round 2 examined the complete 147-file patch and found the six repairs addressed,
then identified one additional Windows probe defect: Preferences is standalone
HTML and never loaded the `waitForDomState` helper called by its acceptance probe.
The probe now joins the page's real `refresh()` Promise before reading readiness
and reopened controls. Existing real-bridge browser Preferences cases invoke that
exact helper against the standalone page, explicitly verifying the missing shared
DOM helper.

Restarted round 3 found a remaining lock interleaving: Setup reuse called menu
refresh while holding the outer application lock, and observer retirement could
join a callback waiting for that lock. Setup now restores/shows and refreshes
outside the lock. Retirement retains registry ownership until close succeeds;
shutdown snapshots the registry and uses the same close/remove boundary, and
quitting prevents new watches. Two real-observer regressions cover the Setup
interleaving and failed-join ownership/retry. These last repairs were made after
the final review and are **not independently reviewed**. No fourth round has
started. The user subsequently requested commit and push explicitly; this
publishes the prepared repairs in draft without representing review clearance.

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

The final Setup/observer retirement repair follows the broader aggregate below;
its ordered checks, focused application/shutdown tests and pending native Setup
acceptance are tracked separately. Do not interpret that earlier aggregate as execution of
the final repair. The final Windows Preferences helper was collected and exercised
in the aggregate browser stage; ordered checks were rerun after its change.

Final repair evidence: ordered Ruff/Pylint/ty/Pyright passed; the application,
updates and shutdown batch passed 123 tests with two native Quit opt-in skips
(`post-cap-repair-validation.log`). Its two new fixtures initially used directory
timestamp changes, which are not the watched content-publication contract; both
passed after publishing actual directory content (`post-cap-repair-focused-2.log`,
two tests, 0.47 seconds). No scheduling delay was added. The Mac is locked again,
so the final native Setup/lifecycle rerun is pending; earlier native results do
not establish this last repair. Final `make ruff` and `git diff --check` pass.

The original repair table contains 35 change groups; the follow-up table assesses
eight review threads. These are not counts of independently reproduced production
races. Current alpha 60 logs, including earlier failures, are retained under
`.tmp/timing-audit/review-followup/`.

- **Broader `make check` passed before the final observer-lock repair** (`restart-r1-make-check.log`): ordered Ruff, Pylint,
  ty and Pyright; Rust workspace formatting, Clippy and tests; 14 real embedded
  scanner/importer tests; runtime-license validation; 18 converter tests;
  **1,418 Python tests** in 502.59 seconds; two end-to-end imports in 12.76
  seconds; **105 browser tests** in 69.72 seconds; website assets/link validation.
- **Skipped checks remain explicit:** 19 Python skips (13 Windows-specific,
  three isolated research, two native Quit opt-ins, one isolated Keychain case)
  and 10 browser native opt-ins. Native Quit was exercised separately below.
- **Focused regressions passed:** 32 Python cases, including real PST imports
  with and without scanning (`focused-1.log`), and two browser cases using the
  actual retained-find driver and the 150,001-row widget (`browser-focused-1.log`).
  The Rust progress test exercises 900,000 checkpoints with 20 diagnostic records
  below 16 KiB; separate real file writes verify the eight-byte work counter.
- **Native Mac checks passed:** lifecycle/menu, HTML find, four setup cases,
  two Preferences and two Quit cases: 10 tests total (`native-1.log`). These
  ran on the unlocked desktop with rebuilt release helpers. They establish
  source execution, not a signed/frozen installer. The restarted lifecycle rerun retains
  `.tmp/timing-audit/review-followup/native-ingests-titlebar-alpha60.png`, visibly
  showing `(1.0.0a60)` at the right. Its blank content proves the native frame only;
  lifecycle/interaction assertions are separate.
- **Website checks passed:** synthetic search/import images were regenerated
  (`website-screenshots.log`), then source/assets validation and the Zola build
  passed (`website-build-final.log`). Browser images and the rendered homepage,
  Searching and Importing pages were visually inspected (`website-site-final.log`
  and `.tmp/website-previews/`). Browser captures do not establish native frames.

Historical alpha 59 evidence remains in `.tmp/timing-audit/no-timers/`. Its
Python stage passed 1,401 tests and its final browser rerun passed 102 tests;
the earlier aggregate was interrupted after exposing additional fixture and
notification defects. Those logs are preserved. The current alpha 60 aggregate
above subsequently passed all stages; it does not inherit a green claim from
the interrupted earlier run.

Published alpha 59 passed ordinary CI; its release-candidate job was skipped.
That result does not cover the local alpha 60 follow-up. No signed package,
installer, appcast or live release acceptance is claimed; no merge, tag or release
is authorized.

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
8. **Independent review is not yet cleared.** The original three rounds were
   exhausted before the strict timer-policy rewrite. The user explicitly restarted
   the capped cycle on November 8; new round 1 found six valid defects, repaired
   above. Round 2 identified an unavailable helper in the Windows Preferences
   probe; it now joins actual refresh completion. Round 3 found one remaining
   Setup/observer lock defect. Its prepared repair is unreviewed; no fourth round
   is authorized. The user subsequently explicitly requested publication; the
   prepared repairs are committed in draft without claiming review clearance.
   Tests do not substitute for independent review.

## Handoff

Alpha 60 and its lockfile are included in the user-requested repair commit for
draft PR #195. The final observer-lock repair is locally tested but not
independently re-reviewed. The final unlocked-Mac native check and Windows/Linux
runtime acceptance remain pending. Publication does not establish readiness.
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
