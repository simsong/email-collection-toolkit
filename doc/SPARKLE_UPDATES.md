<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Native application updates

Issue #91 adds Sparkle to the frozen macOS application. The configured first
target is Apple Silicon on macOS 15 or later. Older macOS and Intel builds need
their own artifact validation; an Info.plist minimum alone does not prove support.
The supported Python/pywebview desktop uses Sparkle on macOS and WinSparkle
on Windows. The Rust GUI and its updater adapters remain historical source.
Source launches never run automatic checks or installers; the Windows adapter
permits manual discovery. Windows archive creation/import uses the shared Python
services, and definition databases live outside archives.

## User behavior

The application menu contains Preferences… (Command-comma) and Check for
Updates…. On Windows, Help contains Check for Updates and Update Settings.
The shared Python preferences retain the track and automatic-check setting.
Stable installations
initially use Release updates. Alpha/beta installations initially use Preview
updates, which adds alpha/beta items to Sparkle's always-included stable track.
An explicit choice survives subsequent upgrades, including preview-to-stable.
Daily checks are enabled initially. Download and installation require confirmation;
automatic-update offers are disabled. Sparkle supplies the standard release-note,
download, error, and Install and Relaunch UI.

The Python preference store retains its version-1-to-2 migration.
Update fields are retained when recent archives change. No archive configuration,
OAuth credential, canonical message, manifest, or telemetry setting is changed.

## Native lifecycle

`updates.py` owns shared channel preferences and the installation reservation.
`sparkle.py` provides the Python macOS native adapter, retaining the Sparkle
controller and delegate and deferring installation while archive work is active.

`winsparkle.py` loads the packaged WinSparkle DLL, supplies the mapped
version/build and Ed25519 key, and installs shutdown/cancel/error callbacks.
Python schedules daily checks for installed builds. Native callbacks remain
retained because SDK cleanup does not join every worker. `windows_update_feed.py`
fetches the shared HTTPS feed with size/time bounds, authenticates its complete
XML signature, and supplies only eligible `ECT.PythonReader` x64 MSIX items over
a private tokenized loopback endpoint. Native WinSparkle verifies the installer
signature and supplies confirmation/install UI.

Installation waits for all document jobs, worker tails, ClamAV definition
replacement, and archive writer leases in other same-user CLI processes. Shared
OS guards live outside archives in a private directory under the user's OS
temporary directory; the exclusive installer guard excludes new processes and
is released on failure or process death. Processes must share that temporary
namespace; this does not coordinate unrelated users or historical client versions.
The archive's existing exclusive lock remains authoritative. An atomic writer gate
then excludes new archive mutations, including archive creation and short
options/identity writes. Failed installation, including raised continuation
errors, clears the reservation. If the user
quits to stop imports while an update is pending, the normal stop/checkpoint
path completes and keeps Cocoa alive for the installer continuation. Only an
idle reserved installation may accept Cocoa termination. Other quit requests
retain the existing orderly shutdown policy.

Runtime Cocoa initialization preserves the numeric version from the shared
`release_versions.py` mapper. The build and appcast use the same mapping.

## Release publication and keys

The app contains the fixed HTTPS feed URL and public Ed25519 key in
`update_metadata.py`. Never generate a replacement key merely to unblock a
build. The release publisher verifies that the exported 32-byte private seed
matches the embedded public key before signing. Legacy expanded exports require
a reviewed migration; they are not silently reinterpreted as seeds.
`make sparkle-tools` verifies cached `sign_update` and `generate_keys` bytes
against the SHA-256-pinned archive before either tool is used.

The build preserves framework symlinks/permissions, signs native executables
and nested helper bundles before the framework and outer app, verifies seals,
and includes Sparkle's complete upstream license text (including its bundled
dependency notices). Build-machine library paths are rejected by the existing
Mach-O audit.

After signing, notarization, stapling, and artifact validation, the publisher
signs/verifies the final DMG, appends the release item, and signs/verifies the
XML feed itself. Release notes are embedded inside that signed feed. New items
declare the minimum macOS version and require `arm64` hardware. The app enables `SURequireSignedFeed` and
`SUVerifyUpdateBeforeExtraction`. Pages copies the signed bytes without XML
rewriting. Both deployment paths cryptographically verify the exact feed bytes
and signed length using the app's public Ed25519 key. This gate uses pinned
pycryptodomex through `uv --no-project`; it requires no private key or native
signer and rejects forged markers, tampering and feeds larger than 16 MiB.
Release assembly must authenticate the previous published feed before appending
or re-signing any history. The tracked unsigned seed is allowed only for the
first-release bootstrap when the pushed tag is the sole `v*` tag. Unsigned legacy
history requires a separately reviewed migration; it cannot enter this release path.

On 2026-09-27 the published `v1.0.0a10/appcast.xml` had no embedded XML
signature, although its enclosure carried a Sparkle signature for the DMG. The
original tag's publisher signed only the archive item; it never ran the Sparkle
signer over the complete XML. Tag-triggered publication is retained, with the
real signer in branch CI and release preflight, signed-history verification
before packaging, and complete DMG/feed validation before draft creation.
Version-dependent test inputs derive from current package metadata; only
explicitly pinned historical fixtures may name an old release. Published tags
stay fixed; retire a failed unpublished tag only by explicit release decision,
then use a new version and tag for the corrected build.
Alpha and beta items use the preview channel; stable items use the default
channel. All use the same signed feed and release workflow. The failed a11 tag
was retired by explicit request; its draft remains as failure evidence. The
candidate version is derived from `pyproject.toml`. The one-time
`make sign-historical-appcast RELEASE_TAG=v1.0.0a10 DMG=... APPCAST=... OUTPUT=...`
migration checks the complete published feed against a pinned SHA-256, then audits
downloaded copies of the exact release item and DMG. It streams the DMG into an
owner-private, read-only temporary copy used for every trust check, mounting,
signature verification, and the mounted self-test. Before executing any
mounted code, it verifies the DMG seal, stapled notarization ticket, and
Gatekeeper assessment, then verifies the app seal (including nested code) and
app-level Gatekeeper acceptance. It checks the mounted app key and bundle
versions against the reviewed a10 release, then verifies the Sparkle
archive signature, then runs the headless self-test against that same
read-only mount through the locked virtualenv interpreter and fixed script. The
interpreter path keeps the virtualenv symlink so imports still work after the
environment is scrubbed. Trust, native dependency probes,
and self-test subprocesses receive no release credentials,
Python/loader/archive overrides, or Apple toolchain-selection variables;
the signer receives no Apple
credentials. The mounted app's `SUPublicEDKey` must match the archive/feed key
before testing or loading the protected Sparkle key. It signs the complete
feed with that key.
It checks the generated feed against the app's public key and refuses to alter
the downloaded source or replace an existing output. It supports only the
embedded release notes; external release notes need their own signature. The
command creates a local signed feed only. Uploading a replacement release asset
and deploying Pages remain separately reviewed publication actions.
The manual `Prepare historical signed appcast` Actions workflow uses this same
Makefile target with the protected key, checks the pinned published DMG digest,
and uploads only a signed XML artifact for review. It does not replace the a10
release asset or deploy Pages.
The historical mounted test checks the a10 app's actual version and embedded
public key, and accepts its pre-Sparkle notice set. Ordinary candidate DMG tests
still require the Sparkle license notice.
For the separate approved publication, preserve the original a10 XML and its
SHA-256, verify the prepared artifact with `make check-appcast
APPCAST=<signed-copy> RELEASE_TAG=v1.0.0a10 ARGS=--require-signed-feed`, upload
that exact XML as the a10 `appcast.xml` release asset, and dispatch the Pages
workflow on `main`. Verify the downloaded release asset and live Pages feed
against the reviewed signed bytes before pushing a new candidate tag.
The completed a10 migration used Actions run `36796346772`; both the release
asset and live Pages feed matched signed XML SHA-256
`37ed3067d1f288bd572a3a6f039fe286ac7a2ffd0e2db755f14c1a9c19568a06`
and passed the public-key verifier. The original unsigned XML SHA-256 was
`de6d09cdc3e2efa508de9ba83d7701addd046660b18bcd3dcf3d5cb49efd8403`.

The private Sparkle key belongs in the protected release secret
`SPARKLE_ED25519_PRIVATE_KEY_BASE64`, with an offline recovery copy. Apple
Developer ID and notarization credentials remain separate. Before publishing,
keep Pages deployments on `main`: the tag workflow dispatches the Pages
workflow there with the exact published tag, and Pages downloads and verifies
that release asset.
Do not change the deployed feed
URL or bundle identifier without an explicit migration.

For key loss/rotation, follow Sparkle's documented Developer ID signed-DMG
rotation path. Do not change Apple identity and Ed25519 identity together. A
replacement feed or missing private key is not evidence that existing clients
can accept the next release.

## Validation and remaining acceptance

`make test-updates` exercises migration/defaults, retained settings, ordered
versions, real writer exclusion, and checkpoint/definition deferral.
`make test-windows-updater` exercises the actual WinSparkle SDK and signed-feed
filtering. Historical Rust updater tests remain separate from Python acceptance.
`[release-ci]` branch CI builds/notarizes the DMG, installs the
shared x64 MSIX, and signs/verifies both final installer bytes
and the complete XML using the production key before a version tag is pushed.
`make test-sparkle-signing` uses the actual pinned signer with a disposable key
and proves archive/feed tampering rejection and public-key matching.
`make sparkle-probe` builds a separate frozen fixture app and invokes its
delegate from compiled Objective-C, verifying track selection, BOOL return,
block ABI, and exactly-once continuation. Its unique defaults domain is removed
afterward. `make sparkle-probe ARGS=--feed-check` additionally performs a real
HTTPS preview check against the existing public feed and inspects Sparkle's
standard update window. It does not download or install an update.

The Windows package CI tests signed installation, Close/Quit, upgrade and
uninstall. This does not prove a complete WinSparkle-driven update; the published
feed does not yet contain a Python Windows release.

Issue #91 remains subject to a signed/notarized older-to-newer application test
using a controlled HTTPS feed. Cover release/preview eligibility, manual/daily
checks, tampered feed/notes/archive, unsupported macOS, interrupted downloads,
busy-archive deferral, relaunch, and unchanged archive/settings/credentials/
definition bytes. The unsigned controller probe and source tests do not prove
that replacement or production signing works. Public releases/settings changes
and changes to machine trust stores require separate owner authorization.

References: [Sparkle setup](https://sparkle-project.org/documentation/),
[programmatic integration](https://sparkle-project.org/documentation/programmatic-setup/),
[settings](https://sparkle-project.org/documentation/customization/), and
[publishing](https://sparkle-project.org/documentation/publishing/).
