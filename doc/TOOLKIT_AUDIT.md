<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Toolkit function audit

This audit reviews the active toolkit at `778133fa82207dc600018cc6e0f1ac34cacd29c7`
and prepares the next alpha's repairs. It covers function bodies, callers,
consumers, substantive tests and corresponding documentation. It is an inspection
record, not proof that every behavior or platform has passed acceptance.

The initial inventory accounted for 2,825 Python functions in 267 files containing
functions (278 Python files total), all 1,417 production Python functions, supported
Rust production code and 54 Rust test functions/helpers, the separate converter,
first-party JavaScript and the Swift helper. Fresh read-only reviewers divided the
inventory by subsystem; an independent complete-patch review followed. The
archived experimental Rust UI, vendored dependencies, private mail/corpora and
historical upstream data were excluded. Documentation comparison was substantive,
not a claim that every historical document or upstream data item was audited.

## Repairs and regression boundaries

| Area | Defect or debt | Repair and useful verification |
| --- | --- | --- |
| Preservation | Recursive MIME or malformed address parsing before filing | Header-only admission, quoted-envelope recovery and MBCP stub checks; raw-reader fallback and independent address-header recovery; exact-byte deep-MIME and later-mail regressions |
| Failure isolation | An old failed metadata job blocks unrelated new sources | Scope message failures; retry eligible saved jobs once without repeatedly selecting failed jobs; retain explicit stop semantics |
| Text/date recovery | Transform codecs and UTC overflow bypass fallbacks | Guard actual decode/conversion; malformed-byte identity and valid date fallback cases |
| Legacy MBOX | Candidate deduplication retains many whole messages | Compact SHA-256 fingerprints in independently implemented readers; late-match memory regressions |
| Source safety | Shared CLI omits folder overlap guard | Shared preflight before CLI/GUI lease and owner-setting writes; directory, child and symlink cases |
| SQLite | Partial DDL/version publication | Explicit schema transaction, foreign keys enabled beforehand; real SQL failure/rollback/retry and repeated indexing |
| MIME readers | Attached EML exports empty; nested attachments become body or amplify memory | Shared byte spans, bounded cached decoding/traversal and Raw fallback; CLI/GUI ancestry, decoded child IDs/text, digest defaults, coalesced delimiters, exact legacy endings and strict transfers |
| Export paths | Windows-invalid basenames and same-name viewer collisions | Portable bounded basenames and per-open directories; actual extracted bytes and concurrent drag ownership |
| Desktop callbacks | Application lock spans synchronous native calls; mutable registries; reused authentication | Snapshot owned webviews, initialize standalone menus, release locks before native calls, and issue fresh viewer bootstrap tickets; real native lifecycle/menu checks |
| Browser state | Old preview/confirmation/settings results overwrite newer state | Request generations and captured message identity; delayed real bridge responses |
| Saved filters | Second rename loses active selection | Compare previous current name; real repeated rename/delete browser scenario |
| IMAP | Bytes/string capability mismatch disables SASL PLAIN | Standard-library string capability; real TLS Unicode login and failed-authentication tests |
| Corpus preparation | Lost MBOX dialect and ignored acquired Git commit | Preserve `.mboxrd`; stream reachable blobs at pinned commit under expansion budget and original 64 KiB classification window; exclude later/unreachable mail |
| Derived PDF | Existence check races with overwrite | Shared native exclusive rename without a hardlink requirement; a real competing output remains untouched |
| AWS worker | UserData encoded twice; obsolete daemon setup | Raw script into actual SDK serialization; embedded scanner configuration and shell syntax check, no live EC2 launch |
| Research readers | Unescaped SQLite paths and stale cache rereads | Shared URI escaping, close-on-setup-failure, hash comparison before provider header analysis |
| Data-quality reports | Empty CSV and population below sample size fail | Schema-independent CSV headers and bounded sample; zero/one/two-candidate fixtures |
| Native packaging | Global Mach-O rpaths hide unresolved dependencies | Separate executable loader chains; real compiled helper/app isolation cases |
| Release identity/cache | dev0/post0 collision and unauthenticated cached resources | Reject suffix presence; compare complete consumed Sparkle bytes/links/inventory against verified archive |
| PST/converter | Virtual search folders and evidence aliases | Exclude virtual folder nodes; reject source/receipt/diagnostic hardlinks |
| Converter contract | Invalid-ID evidence, inherited provenance, long subjects and controls differ | Retain invalid IDs separately and copied provenance; encoded-word folding and DEL/C1 rejection |
| Process regressions | POSIX pipe/signal/process assumptions fail Windows | Bounded portable capture and cleanup, real scanner process observation, console Break, native liveness and portable Quit startup probe |
| Test safety/debt | Arbitrary fixture deletion, open SQLite handles, weak drag/fixity assertions | Refuse unrelated output; close owned connections; assert distinct paths and complete source fingerprints |
| Small duplication | Repeated decode instrumentation, sort handler, search setup and staging name | Shared test helper/read session; remove overridden handler and unused clamdscan parser; unique Swift compilation staging |

Requirements and implementation details are recorded beside the affected subsystem
contracts. Release notes describe user-visible changes. New regressions use synthetic
mail, real SQLite/files/processes and real bridge/SDK serialization. Observational
hooks are confined to deterministic failure or race boundaries and external delivery.

## Findings retained or qualified

- The Rust PST fixture's documented **12 messages and 30 attachments** is correct:
  the real Rust test asserts it. The separate libpff path has different item
  accounting; its result must not replace the Rust fixture oracle.
- Similar source, relocated-package and reader fixtures deliberately cover different
  native launch/provenance boundaries. They were retained instead of merging away
  acceptance coverage.
- Independent verifier implementations remain independent. Shared compact-hash
  policy does not make their parser implementations one implementation.
- Research export permissions still depend in part on the chosen destination's
  umask/ACL. A complete private-publication policy across research tools, especially
  Windows ACLs, is a separate remaining hardening item; chmod alone would not prove it.
- Initial raw retrieval and stdlib parsing still load one selected message in
  memory. Added decoded traversal/cache work is bounded, but this does not
  establish a hard input-memory bound for every reader or optional research tool.
- The original 600-message/28-minute slowdown is not proven. Measured profiles show
  transaction and waiting costs, but cumulative waits overlap and parent profiles
  do not measure OST child internals. Process I/O does not establish disk or antivirus
  causation. No durability policy was weakened to make timing look better.

## Acceptance status

Before the final two review repairs, a full run passed ordered Ruff/Pylint/ty/Pyright, 1,355 Python tests
(19 platform/environment skips), 18 converter tests, Rust workspace checks and
two real import fixtures, and 97 browser tests (10 native opt-in skips). Browser
checks used the already-installed Chrome channel; the default Playwright binary
was absent. Synthetic website assets were rebuilt and visually inspected.
The final review found deferred metadata recovery and filename truncation edge
cases. Their repairs passed fresh ordered lint/types and 277 recovery, processing,
CLI, viewer and cancellation regressions, plus 18 converter tests. These last edits
need independent review before publication; a fresh review cycle was explicitly
authorized after the ten-round audit.
On the unlocked Mac, actual lifecycle/focus and standalone-menu acceptance,
Reprocess, two Quit cases and two native Preferences cases passed. The expanded
native path exposed an incomplete catalog-only fixture and a viewer authentication
failure. The fixture now uses real ingestion; viewers request fresh bootstrap
tickets. Ordered lint/types, 159 GUI/loopback/browser cases and 18 converter tests
passed after those repairs. Earlier locked-session failures remain recorded;
their environmental cause was not proven. The native title-bar image was inspected.
Current-head Windows native frames/menu/version/geometry, relocated import/scanning,
public signing, install/upgrade/uninstall and complete signed appcast remain required.
Earlier-head macOS success does not clear a new candidate. Copilot must actually be
requested and reviewed on the published head. The PR remains draft until those gates
clear; this document does not authorize merge, release or tags.
