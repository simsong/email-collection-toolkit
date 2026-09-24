<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# DevOps

## GitHub Actions

### Intended gates (not yet implemented)

| Workflow | Trigger | Gate and result |
| --- | --- | --- |
| [Continuous integration](../.github/workflows/continuous-integration.yml) | Every push to a non-`main` branch in this repository; PR events only for forked branches | Run `make check` (including all pytest suites), distribution checks, and website validation once per commit. No DMG build, signing, notarization, or release secrets. A PR becoming ready for review does not repeat pytest for an unchanged head. |
| [Website](../.github/workflows/pages.yml) | Every push to `main`, including an accepted PR | Build and deploy the site, preserving the latest published Sparkle feed. No DMG build or release secrets. A merge updates Pages even when the PR changed no website file. |
| [Release](../.github/workflows/release.yml) | Push of an annotated `v...` tag at a version-matching commit already on `main` | Validate the tag before expensive work; build, sign, notarize, staple, and test the DMG once; publish the release with DMG and signed appcast; then build and deploy Pages as a dependent job using that exact appcast. A failed release job must not deploy Pages. |

The `v*` trigger is only a coarse GitHub filter: `make release-tag-check`
enforces the canonical PEP 440 version and exact `v`-prefixed tag. A tag push,
not a PR transition or a merge by itself, is the sole release publication
event. The release workflow should have no manual `workflow_dispatch` path.
Branch protection should require CI and human approval before a PR can merge
into `main`; the release preflight should also confirm that the tag commit is
reachable from `main`. Protect release tags against movement or deletion.

**Ready for review is not approval.** GitHub emits a `ready_for_review` PR
event before anyone approves the change. Building a DMG at that event and
again at the post-merge tag would do the expensive work twice, often for two
different commits. Under the one-build policy, readiness starts human review;
approval plus required CI permits merging; only the later tag starts DMG work.
If pre-merge installed-app validation becomes mandatory, it needs a separate,
explicitly accepted cost (for example, a targeted unsigned smoke build).

To avoid duplicate pytest on same-repository PRs, CI should use branch `push`
events for those branches and not also run the same suite on their
`pull_request` events. Forks cannot trigger a push workflow in this repository,
so a fork-only PR path is needed if forked contributions are supported. The
required CI status must apply to the PR's latest head commit; this plan does
not test GitHub's synthetic merge commit. Keep `main` protected and require the
branch to be current with `main` if that gap is unacceptable. A merge queue
would require a `merge_group` check as well.

The release's Pages job should consume the appcast produced in its own run,
not discover it through GitHub's eventually updated list of release assets.
The ordinary `main` Pages workflow must fetch the latest published feed by
exact tag and fail rather than silently deploying the tracked seed feed if a
published release exists but its asset is temporarily unavailable. Serialize
the two Pages deployment paths so a documentation build cannot overwrite a
newer release feed. The release run should fail if its Pages job or feed
verification fails, even though a published GitHub Release cannot be rolled
back transactionally with Pages.

### Current implementation and required changes

As of this document, these are design gates, **not** a description of the
configured workflows:

* CI currently runs on `main` pushes and on PR events, not every branch push.
  It also builds and mounts an unsigned DMG on each CI run. Change the triggers
  and remove the routine `dmg-smoke` job to meet the one-build policy.
* Pages currently runs only for selected `main` paths, on release publication,
  and on manual dispatch. Remove the path filter and release trigger; keep a
  site-only path for `main` pushes and share its build/deploy logic with the
  release's dependent Pages job.
* Release currently accepts a manual dispatch as well as `v*` tag pushes and
  asynchronously dispatches Pages after publication. Remove manual dispatch,
  add the `main` ancestry preflight, and make Pages a dependent job using the
  appcast from the same run. Its existing tag/version checks, signing,
  notarization, mounted-image test, draft assembly, and publication remain.

See [macOS distribution](MACOS_DISTRIBUTION.md#publish-a-tagged-macos-release)
for the *current* tag command, packaging steps, and secret boundaries. Do not
rely on the intended gates above until the workflow changes have landed and
been exercised.
