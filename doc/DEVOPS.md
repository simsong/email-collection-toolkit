<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# DevOps

## Agreed development and release policy

Primary development takes place on macOS. Minimize GitHub Actions spending by
keeping ordinary development iterations independent of Windows. Bring Windows
to the intended macOS feature fidelity when preparing a release, with limited
local validation on the Windows ARM64 VM and focused release-time checks.

**Implementation status:** this section is the agreed target workflow, not a
claim that it is deployed. The Windows branch preserves macOS-only ordinary CI and provides explicit
manual/release Cargo reader builds for macOS, Windows x64 and Windows ARM64. Windows installer packaging, the mixed-platform appcast publisher, and
the website's two direct-download buttons remain to be implemented. The Rust
Windows application is currently a reader preview; archive writing/imports
remain unsupported. Building an installer does not establish feature parity.

### When to spend runner time

| Event | Required work | Publication |
| --- | --- | --- |
| Ordinary development push | Existing macOS checks, once per commit; Cargo for Rust builds/tests and macOS Makefile wrappers. No automatic Windows build or installer. | None. |
| Explicit test-release run | Build the macOS DMG and Windows installer concurrently from the same selected commit, using the production packaging workflow and focused installed-app checks. | Retain downloadable CI artifacts; no public appcast or current-download changes. |
| Tagged alpha, beta, or stable release | Validate the annotated tag and canonical project version first; build, sign, and validate both installers concurrently from that exact commit. | Publish only after both platforms and all release gates succeed. Alpha/beta publication uses the preview channel. |
| Ordinary website deployment | Build the site using the last complete published release and verified appcast. | Update Pages without rebuilding installers. |

Add a manual test-release entry point that reuses the same packaging jobs as
tagged releases. A manual test run is not itself permission to publish an
update. Published test releases use the normal immutable alpha/beta tag path;
stable clients must not receive previews unless explicitly opted in. Derive
versions, channels, tags, and updater build numbers from `pyproject.toml`
through the shared release-version mapper, including installer-specific version
fields. Never hard-code a candidate version or move a published tag.

Do not run duplicate push/PR jobs or create a Windows build for every macOS
iteration. Explicit test releases are an intentional packaging cost. Build each
architecture once per run and pass those artifacts forward to packaging and
release assembly rather than rebuilding them in the publisher.
Ordinary branch CI assigns the Rust workspace suite to `static-rust`. The
macOS native job runs `make test-rust-gui-native` directly; that target builds
and checks native-feature code and exercises the actual window. It does not
repeat `test-rust-gui` or a separate ordinary reader build.

### Windows distribution and evidence

For explicit pre-merge Windows validation, include `[windows-ci]` in the pushed
head commit message. The branch workflow calls the shared reader workflow for
Windows x64/ARM64 only; its normal macOS job already validates that platform.
Ordinary pushes omit this marker and skip Windows runners. The standalone
manual reader workflow and release caller still build all three platforms.

The planned Windows download is one installer containing native x64 and ARM64
application builds. It selects the matching executable and WinSparkle DLL for
the machine. These remain separate native builds inside a common installer;
the application is not a universal executable. Include license notices,
WebView2 prerequisite detection/installation, shortcuts, and uninstall support.

Keep the macOS native GUI test. Windows release validation should cover install,
launch, opening a synthetic archive, search, message display, unchanged archive
bytes, upgrade, and uninstall. Exercise the packaged application, not only a
Cargo executable. Record the architecture and distinguish hosted checks from
the limited ARM64 VM checks; do not claim x64 execution from an ARM64 build.
Imports and other newly ported features need their own acceptance evidence.
Report untested behavior and remaining parity gaps explicitly.

Windows executable/installer code signing and WinSparkle payload signing are
distinct steps. Preserve the macOS signing, notarization, and installed-DMG
gates. Keep release credentials out of ordinary branch CI and use the same
packaging logic in test runs, reporting any unavailable signing validation.

### One release and one appcast

Retain one shared appcast URL for Sparkle and WinSparkle, including the existing
`/updates/mac/appcast.xml` path for installed-client compatibility. Announce a
version only when both platform installers are complete; do not let two jobs
independently append or publish competing feeds.

Use separate macOS and Windows items, with their own minimum OS versions,
payload lengths, URLs, and signatures. Enclosures use `sparkle:os="macos"` and
`sparkle:os="windows"` for the combined Windows installer. If architecture-specific
installers are introduced later, use `windows-x64` and `windows-arm64` instead.
Extend publisher/checker assumptions that currently allow only DMGs and one
item per tag. Verify prior signed history before classifying historical macOS
entries, then sign and verify the complete updated XML before publication.
Test platform and preview-channel selection in both clients.

Release orchestration is:

1. Validate the release identity, then build and validate both installers in
   parallel from the same commit.
2. Assemble both installers, checksums, and required release assets. Verify
   prior feed history, sign the final payloads, and generate and verify the
   complete shared appcast before creating the complete draft release.
3. Publish the complete GitHub release only after all required gates succeed.
4. Deploy Pages using that exact release's verified appcast and download metadata.

If either platform fails before publication, keep the previous public release,
appcast, and current-download links intact. Serialize publication to prevent
concurrent runs from losing feed history. GitHub Releases and Pages are not an
atomic transaction: if Pages deployment fails after release publication, retain
the failure and retry deployment of the exact published release without moving
its tag, rebuilding installers, or silently resetting the feed.

### Website downloads

Render both platform buttons in static HTML; JavaScript is not required:

- **Download for Mac (.dmg)**
- **Download for Windows (.exe)** — includes native x64 and ARM64 builds
- **View all downloads and release notes** — links to the GitHub release listing

Display **Current release: VERSION** beside the primary buttons. Each primary
button links directly to its installer asset, not an Actions artifact or a
GitHub release-detail page. Generate version, URLs, and availability at site
build time from a complete published release; validate that both assets exist.
Keep preview downloads distinctly labeled and separate from the current stable
release. If only previews exist, label them as previews. Before the first
complete Windows release, do not render an active Windows download button for
an absent asset. Browser platform detection may later emphasize a button but
must not hide the other platform or be necessary for downloading.

## GitHub Actions

### Existing macOS publication baseline

The following describes the macOS workflow being extended. The agreed policy
above governs the planned combined release; the manual test-release path and
Windows packaging must be added rather than inferred from this baseline.

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
release or its asset is unavailable. Release assembly uses the tracked seed
only when its pushed tag is the repository's sole `v*` tag, bootstrapping the
first appcast. After that, it fails rather than resetting update history when
its previous published feed is unavailable or fails public-key signature
verification before history is appended or re-signed.
Both paths check feed structure and cryptographically verify its signature with
the app's embedded public Ed25519 key before
building the site. A shared queued concurrency group serializes Pages builds
and deployments; a documentation build cannot replace a release feed with the
seed. A Pages failure fails the tagged release workflow, although an already
published GitHub Release cannot be rolled back transactionally with Pages.

See [macOS distribution](MACOS_DISTRIBUTION.md#publish-a-tagged-macos-release)
for the tag command, packaging steps, and secret boundaries.

## Local Windows MSIX prototype (2026-10-06)

See [Windows MSIX test packaging](WINDOWS_MSIX_TEST.md) for automated build/sign/test commands,
private Python helper discovery, external WebView2 detection, native Windows
evidence and unresolved installation/import/scanner/converter requirements.
This local prototype is not a released or fully validated Windows application.

## Platform script layout

Keep Windows-only tooling in `scripts/win/`, macOS-only tooling in
`scripts/mac/`, and Linux-only tooling in `scripts/linux/`. Shared scripts remain
in `scripts/`; classify by the operating system required to execute the script,
not by its language or the artifacts it examines. Cargo aliases and Makefile
targets provide the normal entry points. Windows scripts have been relocated;
existing macOS scripts remain at their historical paths until migrated together
with their imports, tests and workflow callers. No Linux directory is needed
until Linux-specific tooling is added.

## MSIX installation matrix (2026-10-07)

The MSIX decision supersedes the older shared WinSparkle feed/EXE plan.
`windows-msix.yml` supports explicit dispatch and reusable release calls; ordinary
pushes do not run it. Two native build jobs produce x64 and ARM64 payloads once.
One assembly job creates a signed common bundle and a higher-version upgrade
fixture with identical application bytes. Both installation VMs download that
same artifact: Windows Server x64 (`windows-latest`) and Windows 11 ARM64
(`windows-11-arm`). Installation jobs do not rebuild. Private signing keys remain
outside uploaded artifacts. Test packages are never published as release assets.
The release caller waits for this gate after tag preflight. Windows 10 testing
is not required. No GitHub Team or AWS provisioning is needed.

Installed checks exercise private Python discovery, synthetic search/fixity,
a native window, upgrade and uninstall, removing test packages and added trust
in cleanup. WebView2 remains external and its absence fails this positive test.
Its writable user-data directory is outside the immutable package. Hosted
execution is pending; earlier local prototype results do not validate this head.
Start-menu activation, missing-runtime UI, Windows imports/scanner/converters,
and physical interaction remain separate acceptance gaps.
