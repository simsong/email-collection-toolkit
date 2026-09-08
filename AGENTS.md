# AI contributor instructions

## Scope and source safety

This project creates a personal, long-lived email archive. Its canonical
content is standard MBOX plus SHA-256 manifests; SQLite databases and search
indexes are rebuildable derivatives.

* Never modify, delete, move, label, mark read, or otherwise mutate a source
  mailbox, Gmail account, IMAP account, Apple Mail store, or input archive.
* Preserve original RFC 5322 bytes. MIME parsing, text extraction, malformed
  MIME handling, and Unicode decoding are best-effort derived operations and
  must never cause a message to be dropped or rewritten.
* A positive ClamAV result routes a message to `INFECTED.mbox`; it is not a
  reason to delete or alter the message. Scanner errors and unscannable input
  must be recorded and retained.
* Do not ingest into a real canonical archive until the user identifies the
  target directory and explicitly authorizes that run. Develop and validate
  against purpose-made fixtures or a copied subset.
* Do not alter local machine configuration, install software, start a
  persistent service, or schedule jobs without explicit user approval. The
  ClamAV daemon is on-demand only; never enable on-access or scheduled scans.

## Requirements and documentation

Before implementing a behavior change, read `doc/requirements.md` and
`doc/implementation.md`. Update both in the same change when behavior, archive
format, recovery semantics, database schema, CLI, or source support changes.

Keep these documents factual and compact. Document the relevant requirement
next to each substantive test or test module. Never claim a feature works
merely because it compiles or emits output: confirm that validation exercises
the stated requirement and report any remaining gap.

## Python implementation

* Use Python 3.12+ and `uv`; add project dependencies through `uv`, not pip.
* All ordinary test and run workflows belong in the Makefile. Use `make check`
  for the full suite; add focused targets only when they validate a distinct
  real behavior.
* Prefer standard-library `mailbox.mbox` for MBOX reading and writing. Pass raw
  `bytes`, not reserialized `email.message.Message` objects, when writing
  archived messages.
* Use typed Pydantic models for internal data. Restrict dictionaries to
  external API boundaries and put external keys in named constants.
* Keep file and database I/O streaming; do not load an archive or attachments
  wholesale into memory.
* Use SHA-256 for canonical message and manifest hashes. Do not substitute a
  hash algorithm, data source, parser, or archive format without explicit user
  approval.

## Tests and validation

At the completion of every Codex turn in this repository, including discussion
and documentation-only turns, run `make ruff` against the current final source.
Ruff must pass with zero diagnostics; if it fails or cannot run, report that
explicitly rather than claiming a clean handoff. Do not suppress rules or ignore
failures to make a build pass. `make check` and `make dmg` must retain Ruff as a required
prerequisite; Ruff complements rather than replaces tests and Pylint.

Write only substantive tests that test a requirement or a demonstrated
regression. No coverage-only tests and no mocks unless unavoidable.

Fixtures must cover byte preservation, mboxrd quoting, malformed MIME, invalid
character encodings, missing dates, duplicate Message-ID with different
content, autosave exclusion, rollover, interruption recovery, and source
idempotence. Use EICAR with the locally configured on-demand ClamAV daemon for
the scanner integration test; use recorded typed scan results only for
unit-level routing tests.

Before reporting a phase complete, review the changed implementation and tests
against every relevant requirement, run the appropriate Makefile target, and
distinguish validated behavior from untested assumptions.

## Git and external actions

Preserve dirty worktrees and unrelated changes. Use project-local linked
worktrees under `<project-root>/.tmp/` for branch work.

Before starting branch work or creating, updating, or merging a pull request,
query GitHub for every open pull request in the repository. If any are open,
warn the user before proceeding and identify each PR's number, title, base, and
head. Check for overlapping files or commits, stale bases, and changes already
integrated or superseded by another PR; never silently duplicate or overwrite
open PR work.

Every push to a GitHub branch other than `main` must have a matching open pull
request whose head is that exact branch. Before pushing, check for that PR; if
none exists, create it as part of the same publication workflow. After pushing,
verify that the PR head matches the pushed commit. Never leave work only on a
GitHub non-`main` branch, including when a previously merged branch name is
reused.

## Authorization and identity

A request to perform **pr-to-ready**
authorizes the commits, pushes, draft PR creation, review requests, review-thread
replies, fixes, and final human-review request described below. Continue through
these steps without repeatedly requesting permission. Do not approve or merge a
PR, close an issue or superseded PR, or change remote services without explicit
user authorization for that action.

All Codex GitHub writes and browser actions must use `@simsong-codex`, never
`@simsong`. Verify the CLI identity, SSH push identity, and browser login
separately. Before committing, configure and verify author and committer as
`Codex AI Assistant <simsong+codex@acm.org>` and verify the signing key belongs to
that identity. Sign every Codex commit. Verify the result before pushing with
`git log -1 --format='%G? %GS %an <%ae> %cn <%ce>'`. To correct identity on an
existing commit, use `git commit --amend --reset-author -S`.

## pr-to-ready

1. Fetch current remote state; inspect the intended diff against the PR base and
   preserve unrelated work. Run the relevant Makefile validation before each
   commit. Commit, push, and open a matching **draft** PR in the same publication
   workflow. Verify that GitHub's PR head SHA equals the pushed commit.
2. Open the PR in an authenticated web browser and verify `simsong-codex` is
   signed in. In the GitHub reviewers panel, click Copilot's **Request** or
   **Re-request review** control. Do not use `@copilot review`, another mention,
   a CLI command, or an API request to trigger a review. If the browser login or
   control is unavailable, report the exact blocker and keep the PR draft.
3. Verify a visible pending Copilot review or review-request timeline event.
   A click alone is not proof. Check for Copilot's response every **10 minutes**,
   reconciling the live head SHA, review events, review threads, and CI checks.
   Use a task heartbeat when continuation beyond the current turn is needed;
   keep it quiet while nothing changes and stop it when the cycle completes.
   Do not abandon the cycle after requesting a review or pushing a fix.
4. Read every finding and reply in its **exact GitHub review-panel thread**.
   Fix valid findings. For an incorrect finding, explain the relevant invariant
   and evidence; clarify source comments when that explanation helps future
   readers. Do not add misleading comments or weaken correct behavior merely
   to satisfy Copilot. After pushing a fix, add its commit SHA and Makefile
   validation evidence to that same thread. Do not manually resolve Copilot's
   threads; distinguish automatic resolution from verified correctness.
5. After every new push, request another Copilot review using the browser
   control and repeat the 10-minute checks. Previous-head reviews do not clear
   a new head. Address CI failures as well as review findings. A submitted
   review with no remaining actionable findings counts as successful; Copilot
   need not submit an approval verdict.
6. Continue until the current head has a successful review, or a documented
   loop remains: the same substantive finding returns in two successive review
   rounds after an evidence-backed fix or explanation, with no new actionable
   information. Record the relevant threads, commits, evidence, and remaining
   disagreement. A missing review, failed check, or unresolved valid defect is
   not a review loop and must remain a reported blocker.
7. Once required CI and relevant local validation pass, mark the PR **ready for
   review** and request review from `@simsong`; also assign the PR to `@simsong`.
   For a loop handoff, explicitly say that Copilot is not clear and identify the
   disputed findings for human judgment. Verify ready state, reviewer request,
   assignment, and final head on GitHub. Never approve or merge automatically.

Completion means the verified human-review handoff, not merely a successful
push, request click, or comment. Report the PR, head, validation, review outcome,
and any remaining limitation accurately. Preserve progress and the exact next
step when an external blocker prevents completion.

## Validation and cleanup

Use Makefile targets for Ruff and Pylint linting, then ty and Pyright type
analysis, then pytest. Keep these stages ordered in the aggregate check target,
even under parallel make. Treat all diagnostics as failures; fix the underlying
logic or precise types rather than broadly excluding files, disabling checks,
or adding blanket ignores. Add focused third-party stubs only where needed.
Update requirements, implementation documentation, and release notes when the
corresponding behavior or developer workflow changes. Report skipped tests and
external prerequisites separately from passing validation.

After a branch is merged into `origin/main`, fetch and prune, prove its work is
represented in the current main (ancestry, or explicit squash/rebase evidence),
and verify the linked worktree has no modified or untracked files. Only then
remove its linked worktree and delete its local branch. Preserve dirty,
unmerged, or uncertain worktrees. A superseded PR closed during consolidation
is not proof that its branch has reached main.
