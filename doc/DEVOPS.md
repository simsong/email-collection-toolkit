<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# DevOps

## Agreed development and release policy

Primary development takes place on macOS. Minimize GitHub Actions spending by
keeping ordinary development iterations independent of Windows. Bring Windows
to the intended macOS feature fidelity when preparing a release, with limited
local validation on the Windows ARM64 VM and focused release-time checks.

**Implementation status:** Python/pywebview is the supported desktop. Explicit
`[release-ci]` branch checks build the Apple Silicon DMG and x64 Python MSIX,
validate installation and upgrades, and authenticate both payloads and the
shared appcast before a tag may be pushed. Windows creation/importing and
processing use shared Python services; hosted installed-package checks remain
required for each candidate. ARM64 packaging and the Rust GUI are historical
plans, superseded by the Windows consolidation below. Current source version
and release identity derive from `pyproject.toml`; this document is not evidence
that a candidate has passed CI or been published.

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
Ordinary branch CI checks the maintained Rust tools in `static-rust` and the
Python/browser interface in `python-browser`. The retired Rust GUI is excluded
from the workspace and has no active native CI or release dependency. Opted-in
release candidate checks build the Python DMG and Python MSIX packages.

### Windows distribution and evidence

For explicit pre-merge packaging validation, include `[release-ci]` in the pushed
head commit message. The branch workflow reuses the same macOS and Windows
packaging jobs as the tagged release and verifies their combined signed feed.
Ordinary pushes omit this marker and skip installer builds. The Windows workflow
also supports explicit dispatch for its x64 package checks.

The supported Windows download is a test-signed x64 MSIX bundle with private
CPython, ClamAV and WinSparkle. Its trust ZIP supplies the persistent public
certificate and installation instructions. WebView2 remains an external
prerequisite. ARM64 package construction is not part of the current workflow.

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

Generate installer metadata from public GitHub releases through
`make website-release-data RELEASES_JSON=PATH`. JavaScript selects the primary
button from browser platform hints:

- **Download macOS installer** or **Download Windows installer** links to the
  matching uploaded asset.
- **Download the installers** links to the generic releases page when the
  platform cannot be identified.
- **Show all installers** always links to the generic releases page beside it.

Missing platform assets use the generic page with an availability notice.
Explicit platform links and the generic fallback work without JavaScript.
The page states Apple Silicon macOS and x64 Windows requirements; browser hints
cannot prove hardware compatibility. Prefer complete stable releases, falling
back to clearly labeled previews before the first stable release. Nonempty,
exactly named DMG/MSIX assets, the Windows trust ZIP and authenticated appcast
are required for a complete release; a requested publication tag must be
complete or Pages fails. Historical Mac-only releases retain their Mac links.

The update-stream selector belongs in application Preferences, shared by
Sparkle and WinSparkle: **Release only** or **Alpha / beta / development and
release**. Development updates mean published previews. Preferences persist
outside archives; there is no stream selector or saved update setting on the
website.

## GitHub Actions

### Current publication workflows

The reusable candidate workflow validates both packages before the immutable
tag. The tag workflow repeats identity and packaging gates for publication.

| Workflow | Trigger | Gate and result |
| --- | --- | --- |
| [Continuous integration](../.github/workflows/continuous-integration.yml) | Push to a non-`main` repository branch | Ordinary checks skip changes limited to `README.md` and `doc/RELEASE_NOTES.md`. Other changes run static/tests, distribution and website validation. An explicit `[release-ci]` head independently runs DMG/MSIX/signature gates on any branch without publishing. |
| [Website](../.github/workflows/pages.yml) | Push to `main` that changes files outside `README.md` and `doc/RELEASE_NOTES.md`, or manual `workflow_dispatch` | Build and deploy the site, preserving the latest published Sparkle feed. No DMG build or release secrets. Other documentation changes still deploy Pages. |
| [Release](../.github/workflows/release.yml) | Push of an annotated `v...` tag at a version-matching commit already on `main` | Validate the tag before expensive work; build, sign, notarize, staple, and test the DMG once; publish the release with DMG, MSIX, Windows trust material and signed appcast; then build and deploy Pages as a dependent job using that exact appcast. A failed release job must not deploy Pages. |

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
frozen Python reader validation, external WebView2 prerequisites, native Windows
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

## Historical MSIX installation matrix (2026-10-07)

MSIX replaces the older EXE installer plan. The Python desktop is now its
entry point; the a15 Rust WinSparkle adapter is archived and no longer ships.
Windows automatic-update support remains unfinished. `windows-msix.yml`
supports explicit dispatch, reusable release calls and explicit packaging-branch
`[msix-ci]`/`[release-ci]` pushes; ordinary pushes do not run it. Two native build
jobs produce x64 and ARM64 payloads once per workflow invocation.
One assembly job creates a signed common bundle and a higher-version upgrade
fixture with identical application bytes. Both installation VMs download that
same artifact: Windows Server x64 (`windows-latest`) and Windows 11 ARM64
(`windows-11-arm`). Installation jobs do not rebuild. Private signing keys remain
outside uploaded artifacts. The authorized alpha may publish the validated base
test-signed bundle and public trust material; upgrade fixtures stay CI-only.
The release caller waits for this gate after tag preflight. Windows 10 testing
is not required. No GitHub Team or AWS provisioning is needed.

Installed checks exercise frozen Python search, autocomplete and byte retrieval,
a native window, upgrade and uninstall, removing test packages and added trust
in cleanup. WebView2 remains external and its absence fails this positive test.
Its writable user-data directory is outside the immutable package. Hosted matrix
execution is implemented, including Start-menu activation and missing-runtime
guidance. Results must match the candidate head; earlier package results do not
clear revised signing or updater code. Windows imports/scanner/converters and
physical interaction remain separate acceptance gaps.

Windows test signing now requires the persistent `MSIX_TEST_CERT_PFX_BASE64`
Actions secret, passed only to bundle signing (including reusable release calls).
The signer checks it against `scripts/win/test-signing.cer`, requires a private
key and current validity, and removes temporary PFX material on success/failure.
No fallback certificate is generated. This supersedes the ephemeral test-key
policy; testers trust the public certificate once until expiration (2028-10-07)
or deliberate rotation. Production trusted signing remains separate.

## Windows consolidation (October 10, 2026)

The Windows Python work is integrated into PR #153 while retaining the Mac restoration and historical Rust GUI. The supported Windows package is x64-only private CPython with ClamAV and WinSparkle. This supersedes earlier frozen-Python/ARM64 and unsupported-import descriptions in the historical sections. The signed feed publisher labels Windows items with the Python package identity and x64 architecture so the updater can select compatible installations. The native install test retains both last-window Close and File/Quit checks.

The installed reader CI regression was a Windows fsync on a read-only file descriptor. Publication now syncs a writable handle, and the package's actual self-test uses the shared byte-preserving MBOX class. Source acceptance covers both script and isolated module entry points, processor discovery, search/completion, and byte hashes. Current-head signed installation still requires hosted CI evidence.
