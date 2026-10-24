<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Threading audit

Finite work retains a future and publishes its result on the owning coordinator.
Source generators stay on their worker threads because input readers can own
thread-bound SQLite handles; publication requests travel back through futures.
Each conversion below has its own commit. This audit does not restore support
for the retired Rust desktop.

| Converted work | Owner and result handling | Validation target |
| --- | --- | --- |
| Native scanner calls | Scanner process collects typed responses and alone writes IPC | `test-clamav` |
| GUI previews | Current-generation window polls collect results under the preview lock | `test-gui` |
| Source acquisition | Driver executes captured publication requests; catalog, search and processing SQLite enforce thread ownership | `test-source-coordination` |
| GUI imports | Named futures retain worker lifetime; Quit cancels queued imports and releases leases | `test-quit`, `test-native-quit` |
| Cocoa archive Open | Application command executor retains and collects outcomes | `test-native-quit` |
| Cocoa About | Same command executor and error notices | `test-native-quit` |
| Cocoa Setup | Same command executor and error notices | `test-native-quit` |
| Startup stale exports | Same command executor and error notices | `test-native-quit` |
| Private adapter imports | Protocol owner collects typed status and warning outcomes | `test-rust-engine` |
| Private adapter definition updates | Protocol owner collects updater failures once per generation | `test-rust-engine` |

The processing dispatcher and index refresh already consume futures. OCR
experiments use `as_completed`; name-observation research consumes a process-pool
map. They retain their existing result-collection patterns.

## Dedicated threads retained

| Location / work | Reason it remains independent |
| --- | --- |
| `gui_app.ConnectivityMonitor`, `imap_cli.StatusReporter` | Long-lived timed reporting loops, rather than finite result-producing tasks |
| `gui_app.NativeSmoke` watchdogs, `self_test` timer | Deadline enforcement must run even if workers are blocked |
| GUI Setup Cancel and `winsparkle` Quit dispatch | Shutdown admission must work with a saturated command pool |
| GUI Quit watchdog, waiter, export cleanup and shutdown display | Each must progress independently of command workers and blocked UI callbacks; display already collects per-window futures |
| `scanner` owner watcher and result reader | Parent-death enforcement and continuous IPC consumption cannot wait behind scans |
| `owned_command`, `windows_scanner` owner watchers | Independent process supervision, including terminating blocked native work |
| `rust_engine` owner-pipe reader | EOF must cancel/terminate a blocked protocol operation |
| `loopback`, `windows_update_feed` HTTP servers | A server owns its request loop and its shutdown lifecycle |
| `winsparkle` scheduler | Periodic service loop with a stop signal |
| Cocoa main-queue dispatch | Native UI thread affinity requires main-queue execution |
| Retired Rust search/completion/worker/bridge/engine readers | Persistent channel consumers and actor-owned state already deliver results to the UI owner |
| Retired Rust update gateway/scheduler and native event loops | Server, timer and OS event-loop ownership rather than queued computations |
| Retired Rust desktop child reaper, drag cleanup and Quit/smoke guards | Child lifetime, cleanup and deadlines must remain independent of reader work |
| Test/probe threads and disposable IMAP/HTTP fixtures | Own event loops or deliberately create concurrent/blocking boundaries; moving them into the tested pool would change the failure scenario |

Locks remain for short shared-memory bookkeeping and native protocol boundaries.
Durable source publication no longer runs on arbitrary acquisition threads under
a publication mutex. Scanner request transmission and result bookkeeping use
separate locks to allow both directions to drain under IPC backpressure.
