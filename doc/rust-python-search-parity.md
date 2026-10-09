<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Python and Rust search parity audit

Audited local revision: `c85df4de68ad05f7667e17481501add94e0cdd18`, branch
`work-rust-gui`. This report describes that candidate, including unpublished work;
it is not a claim about the current GitHub PR head or a released installer.

Rust should adopt Python's **filter-before-sort query strategy and search
contract**, retaining Rust's separate cancellable worker and bounded display
pages. The performance problem is avoidable SQL work, rather than evidence that
Rust itself is slow. Runtime alignment and a permanent shared acceptance matrix
are the next implementation work; they have not been delivered by this audit.

## Execution paths

Both native applications use `gui/app.js`, but it selects different API paths.

| Step | Python GUI | Rust GUI |
| --- | --- | --- |
| Query assembly | `effectiveQuery()` combines selector chips and text; folder selections, attachments and sort accompany the request. | Same frontend assembly. |
| Dispatch | `runCompleteSearch()` calls `GuiApi.search()` for up to 2,000 rows; if necessary, automatically fetches the remaining headers. | Calls `search_start`; polls status, acknowledges preview windows, and requests display pages. |
| Parsing | `gui_service.search_page` → `mailsearch.parse_query` → typed `SearchTerms`; `search_selectors` recognizes and normalizes selectors. | `selectors::plan` tokenizes and constructs bound predicates, values and highlights. |
| SQL | `mailsearch._search_statement` and `_count_statement` share `_search_predicate` and `_candidate_source`; selector predicates come from one registry. | Predicates are shared, but complete SQL is independently assembled in `Bridge::search`, `Bridge::search_batch`, `search::remainder_query` and `browse` counts/suggestions. The egui comparison reader has another simpler query. |
| Initial work | GUI explicitly passes `find_older=True, complete_sort=True`: relevant filtering indexes select matching candidates before ordering and header aggregation. | Always scans two sorted 512-message catalog windows, tests their candidates, and waits for frontend acknowledgement of each, including empty windows. |
| Completion | If the first response has more matches, a second complete query fetches the remainder. | Only after both windows, one query collects remaining ordered IDs using a sort-value/message-ID cursor. |
| Rendering/read responsiveness | Frontend rejects stale responses; previews use a separate Python executor. | Separate read-only search connection and generation cancellation; foreground hydrates at most 512 headers per page. Initial display loads at most 1,024, then scroll loads more. |

The Python CLI's optional recent-text shortcut is **not** the Python GUI search
path. Comparing Rust's two mandatory windows with that CLI shortcut would obscure
the regression. Likewise, `Archive::search` is the old egui comparison path, not
the primary native Rust frontend's staged search.

Python has one shared search compiler stack, not one function containing every
SQL statement in the application: autocomplete still assembles its own aggregate
queries while reusing selector predicates. Rust needs the same ownership boundary
for complete search statements, rather than SQL strings scattered across callers.

## Optimizer and index evidence

The existing Python acceptance test
`test_search_primitives_use_filter_indexes` supplies 21 cases: Any sender and
recipient; From hit/miss; To/Cc/Bcc; Date/Before/After; one term, phrase and AND;
attachment hit/miss; combined selectors/text; subject hit/miss; and folder,
volume and combined folder/volume selections. It checks three sorts, both
directions, unlimited counts and threshold counts: **252 production statements**.
It binds the actual builder's unchanged parameters to `EXPLAIN QUERY PLAN`,
executes each statement, and limits VM work on a 20,001-message sparse fixture.

For this audit, those exact cases and the same Python-created archive were fed
to a temporary Rust unit diagnostic calling the actual private
`remainder_query` builder, in all six sort/direction combinations:
**126 production statements**. All returned the expected matching IDs.
**68/126 exceeded Python's VM-work budget.** The strict Python index-name/plan
checks disagreed with 102/126; this is not 102 separate defects. Some differences
are efficient alternative composite indexes and should remain acceptable.

| Operation | Python VM instructions | Rust full-stage VM instructions | Interpretation |
| --- | ---: | ---: | --- |
| Any recipient | 100–300 | 180,100 | Indexed membership subqueries exist, but Rust still traverses unrelated category rows. |
| From sender | 100–300 | 100–260,100 | Rust's subject/sender sorts filter efficiently; date sorts choose the date/cursor path and visit unrelated rows. |
| To recipient | 100–300 | 180,100 | Python resolves matching addresses then recipient membership; Rust's outer candidate access defeats sparsity. Cc/Bcc behave similarly. |
| Body term | <100–200 | <100 | Rust's remainder already uses FTS MATCH plus `messages_sha256`; retain this. Its mandatory preview overhead is additional. |
| Body term with attachments | <100–300 | 1,180,000–1,260,000 | Python unions indexed message/attachment hashes. Rust performs correlated rowid/FTS probes per catalog candidate. |
| Subject substring | 100,000–100,200 | 120,000–200,000 | Python scans the covering subject expression index then fetches matches. Rust evaluates the subject predicate while traversing message rows. A leading-wildcard substring legitimately requires a covering scan. |
| Folder/volume selections | <100–200 | 180,000–180,100 | Rust does not consistently choose the source hierarchy/volume path and message-rowid membership strategy. |
| Date selector | <100–200 | <100 | Rust uses `messages_category_date_message` for filtering. This is a valid efficient alternative to Python's forced `messages_date_message`. |

Counters sample every 100 SQLite VM instructions; a reported zero means fewer
than 100 observed instructions, not zero work. Python ranges include bounded
header queries and counts; Rust ranges cover the IDs-only full-stage query, which
should not need more work merely to omit display columns. The Python and Rust
SQLite engines were **3.53.4 and 3.50.2** respectively. VM instructions are a work
diagnostic, not directly comparable wall-clock durations or proof of a specific
speedup. SQLite version, planner and build configuration also differ.

The Rust diagnostic cursor was deliberately positioned before all rows, so it
measures the complete query's access strategy. It does not measure preview SQL or
the exact cursor produced after two previews. Separate real staged-RPC comparisons
executed both preview stages and completion for the same 126 case/order pairs.
All those matched Python's IDs; these sparse cases have zero or one result and do
not by themselves establish tie ordering or pagination.

Relevant index responsibilities:

| Filter/order | Required useful access |
| --- | --- |
| Body/attachment text | FTS MATCH → distinct canonical hashes → `messages_sha256`. Per-message FTS MATCH is insufficient. |
| From/Any sender | Matching addresses → sender lookup; `messages_sender_address_pk` or an efficient category/sender composite search. |
| To/Cc/Bcc/Any recipient | Matching addresses → `recipients_address_pk` → catalog rowid; preserve exact roles and deduplicate membership. |
| Date/Before/After | Selective UTC range search through date or category/date index under every display sort. |
| Subject contains | Covering `messages_subject_message` scan, followed by rowid lookups for matches. |
| Folder/volume | Appropriate `source_files_hierarchy_volume` or `source_files_volume_hierarchy`, then `observations_source_file_offset`, then message rowids. |
| Unfiltered ordered browsing | Date, subject or sender ordering indexes; useful here, but not a reason to scan unrelated rows in selective searches. |

“All indexes are used” means the appropriate indexes for each operation, with
bounded work. It does not mean every query uses every index, or that `SCAN USING
INDEX` and correlated probes automatically establish efficient filtering.

## Functional comparison

The real Python GUI service and compiled optimized Rust `--rpc` dispatcher were
compared across **181 operations**. There were **17 differing responses**,
including repeated manifestations of the same issue. The 126 staged optimizer
case/order pairs, eight body/attachment intersection cases, and three header/live
name searches agreed. No native windows were opened.

| Behavior | Evidence and disposition |
| --- | --- |
| Basic language | Ordinary ANDed terms, phrases, roles, combined selectors, supported worldwide dates, ASCII case-insensitive matches and unknown `ticket:123` text agreed in the probed cases. |
| Attachment intersection | `Plain Appendixquartz` can match body plus attachment when enabled; attachment-only text is excluded when disabled. All eight probes agreed. Query efficiency still differs sharply. |
| Selector normalization | `subject:" planning "` finds the message in Python but not Rust. Python strips and casefolds selector values; Rust preserves them. Align parser normalization after testing Unicode behavior explicitly. |
| Empty quoted term | Python `""` and `''` return no matches; Rust discards the token and returns all searchable messages. Preserve the distinction between an empty query and an explicitly empty term. |
| Dates | Rust accepts `date:2024-1-3` and year zero while Python rejects them. Supported complete spellings share the same 50-hour worldwide range; enforce the same recognition domain. |
| Date suggestions | Counts agreed, but Python normalizes slash/month date suggestions to ISO values; Rust retains original input. Align normalized values/labels. |
| Highlights | Rust retains duplicate terms, selector case and input order; Python deduplicates and groups text before selector values. Align the response contract. |
| Original folder tree | Rust exposes individual loose `.eml` leaves that Python intentionally collapses into their parent mailbox. Volume ordering/grouping also differs. Both tested tree responses differed; align source-kind handling and deterministic grouping. |
| Attached-message badge | A real processor-resumed child message is `attached_message=True` in Python's search rows, but False in Rust. All Rust header hydration paths currently hardcode False. Align derived attachment tagging. |
| Header/live names | Header names, renamed canonical person names and address matching agreed in three probes after a real fixture identity edit. |
| Institution names | Rust additionally includes organization-domain names in its temporary address-name view; `any:"Audit Institution"` found three fixture messages in Rust and none in Python. This is a possible useful Rust enhancement; decide whether to keep it and extend Python's contract instead of silently removing it. |
| Counts | Python exposes production count/threshold SQL, used by its tests and service API. The current shared frontend does not call `search_count`; Rust's active-search count comes from retained matching IDs. Rust lacks an equivalent general production search-count builder and its full optimizer matrix. Do not add an unnecessary count query before showing results. |
| Suggestions | Basic role/name suggestions agreed in 11/15 probes. Rust's optional 150-ms foreground deadline is an additional policy: timeout is explicitly incomplete, never a fabricated zero count. Preserve this distinction in tests. Ranking ties and Unicode normalization require additional shared cases. |
| Stable ordering/cancellation | Existing Rust unit tests exercise all six sort/direction combinations, tied values, duplicate FTS hits, stale generations and staged completion; browser regressions exercise paging/selection. Retain them. The sparse parity matrix is not a replacement for those tests. |

Results-row rendering also differs in recipient separator/aggregation formatting:
Python groups distinct addresses after filtering; Rust uses per-row recipient
aggregation. This audit's matrix compares matching IDs, not every display field.
It is not a certification of all Unicode, quoting/escaping, ranking, duplicate
observation, folder-token, concurrent-writer or live GUI performance cases.

## Alignment plan

1. **Centralize production Rust search SQL and fix the selective execution path.**
   Introduce typed parsed queries and bound statements in one Rust module. Full
   ID selection, direct search, result pages and count variants must consume that
   compiler. Select filtering paths before sorting; use independent indexed
   message/attachment hash membership, recipient membership, covering subject
   matching and indexed folder observations. Fetch display metadata after selecting
   matches. Keep safe parameter binding, quarantine exclusion and stable tie keys.
   Remove mandatory catalog-preview scans for selective searches. Return matching
   pages from the indexed query while retaining the separate worker and generation
   cancellation. Update the existing requirements/implementation language that
   currently mandates two catalog windows in the same behavior-change commit.
2. **Align response semantics and common search operations.** Normalize selectors,
   dates and highlights; retain explicit empty tokens; share correct header
   hydration including child-message tags; align loose-message tree collapsing,
   counts and ordering. Evaluate institution matching as an intentional enhancement.
   Reuse selector predicates in completion and folder-count queries. Keep Rust's
   bounded display loading and explicit incomplete/error states.
3. **Make the shared contract a permanent gate.** Put the cases/expected outcomes
   in one language-neutral fixture consumed by Python acceptance tests and Rust
   unit tests, including production page, complete-ID and count SQL. Bind unchanged
   production parameters to EXPLAIN; assert useful filtering/index families and
   VM budgets, admitting demonstrated efficient composite alternatives. Expand
   fixtures for ties, multi-page results, duplicate observations, cross-body/
   attachment AND, names/institutions, child tags, token normalization and parser
   boundaries. Retain existing real browser concurrency/paging regressions. Then
   benchmark the optimized packaged Rust reader and Python GUI with alternating
   runs, controlled warm/cold conditions and separate time-to-first-results,
   completion, header-fetch and rendering measurements.

The first performance chunk is SQL centralization and indexed result selection.
Installer/updater corner-case expansion is not needed to establish search parity.
No new search-speed improvement is claimed until the changed execution path passes
these gates and measurements.

## Validation and artifacts

- `make test-mailsearch`: **84 passed**, including all 63 parameterized optimizer
  tests/252 statement checks.
- `make test-gui`: **77 passed**, covering GUI services and completion.
- `make test-rust-gui ruff`: existing Rust format/Clippy/unit gates and final Ruff
  passed. The Rust gate has three ignored standalone fixture tests; five child
  invocations are exercised by parent tests, separately visible in its log.
- Temporary `search-audit-rust` diagnostic: **failed as intended**, recording the
  current 102 strict plan mismatches and 68 budget failures. It was attached only
  during execution, then the exact signed source bytes were restored. This is not
  a newly passing permanent Rust test or evidence that the alignment is complete.
- `search-audit-semantics`: 181 real service/dispatcher comparisons completed.
  Successful Rust suggestions must explicitly be complete before comparison;
  a timeout cannot be counted as parity.

Reproduction harness, synthetic optimizer fixture and JSON evidence are local in
`.tmp/search-parity/`: `Makefile`, `prepare.py`, `audit.rs`, `run.py`, `compare.py`,
`cases.json`, `python-plans.json`, `rust-plans.json`, `semantics.json` and logs.
Use `make -f Makefile -f .tmp/search-parity/Makefile search-audit-prepare` after
the Python suite has created its fixture; invoke the temporary Rust diagnostic
through `run.py` so restoration is guarded. The semantics target creates fresh
fixture directories per run. Do not attach the diagnostic to production source
permanently or weaken its assertions to hide the observed failures.

No real mailboxes or canonical archives were changed; no native application,
GitHub write, review monitor, push, merge, tag or release was started for this
audit. Existing a15 publication/review gates remain separate and uncleared.
