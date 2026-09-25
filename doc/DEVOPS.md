<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# DevOps

## GitHub Actions

### Workflow gates

| Workflow | Trigger | Gate and result |
| --- | --- | --- |
| [Continuous integration](../.github/workflows/continuous-integration.yml) | Every push to a non-`main` branch in this repository | Run `make check` (including all pytest suites), distribution checks, and website validation once per commit. No DMG build, signing, notarization, or release secrets. A PR becoming ready for review does not repeat pytest for an unchanged head. |
| [Website](../.github/workflows/pages.yml) | Every push to `main`, including an accepted PR | Build and deploy the site, preserving the latest published Sparkle feed. No DMG build or release secrets. A merge updates Pages even when the PR changed no website file. |
| [Release](../.github/workflows/release.yml) | Push of an annotated `v...` tag at a version-matching commit already on `main` | Validate the tag before expensive work; build, sign, notarize, staple, and test the DMG once; publish the release with DMG and signed appcast; then build and deploy Pages as a dependent job using that exact appcast. A failed release job must not deploy Pages. |

The `v*` trigger is only a coarse GitHub filter: `make release-tag-check`
enforces the canonical PEP 440 version and exact `v`-prefixed tag. A tag push,
not a PR transition or a merge by itself, is the sole release publication
event. The release workflow has no manual `workflow_dispatch` path. Its
preflight confirms that the tag commit is reachable from `main`. Repository
rulesets should separately require CI and human approval before merging into
`main` and protect release tags against movement or deletion; the workflow
files alone cannot impose those repository settings.
The `github-pages` environment must permit deployments from both `main` and
`v*` tags; checking out `main` inside a tag-triggered job does not change its
deployment ref. This environment rule is also a repository setting.

**Ready for review is not approval.** GitHub emits a `ready_for_review` PR
event before anyone approves the change. Building a DMG at that event and
again at the post-merge tag would do the expensive work twice, often for two
different commits. Under the one-build policy, readiness starts human review;
approval plus required CI permits merging; only the later tag starts DMG work.
If pre-merge installed-app validation becomes mandatory, it needs a separate,
explicitly accepted cost (for example, a targeted unsigned smoke build).

To avoid duplicate pytest on same-repository PRs, CI uses branch `push` events
and does not also run on `pull_request`. Forked branches do not push into this
repository and therefore do **not** receive this CI gate; add a fork-only PR
path before accepting forked contributions. The required CI status must apply
to the PR's latest head commit; this workflow does not test GitHub's synthetic
merge commit. Keep `main` protected and require the branch to be current with
`main` if that gap is unacceptable. A merge queue would require a
`merge_group` check as well.

The release's Pages job consumes the appcast produced in its own run, not
GitHub's eventually updated list of release assets. An ordinary `main` Pages
run selects the latest published release by tag, retries downloading its
appcast, and fails rather than silently deploying the tracked seed if that
release or its asset is unavailable. Release assembly also fails rather than
resetting update history when its previous published feed is unavailable.
Both paths check feed structure and signature metadata before
building the site. A shared queued concurrency group serializes Pages builds
and deployments; a documentation build cannot replace a release feed with the
seed. A Pages failure fails the tagged release workflow, although an already
published GitHub Release cannot be rolled back transactionally with Pages.

See [macOS distribution](MACOS_DISTRIBUTION.md#publish-a-tagged-macos-release)
for the tag command, packaging steps, and secret boundaries.
