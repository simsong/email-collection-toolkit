<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# macOS updates

Issue #91 adds Sparkle to the frozen macOS application. The configured first
target is Apple Silicon on macOS 15 or later. Older macOS and Intel builds need
their own artifact validation; an Info.plist minimum alone does not prove support.
Source-checkout launches and other platforms report updates unavailable and do
not start Sparkle or automatic network checks.

## User behavior

The application menu contains Preferences… (Command-comma) and Check for
Updates…. The native Updates pane shows the installed version/build, track,
automatic-check preference, last check, and updater status. Stable installations
initially use Release updates. Alpha/beta installations initially use Preview
updates, which adds alpha/beta items to Sparkle's always-included stable track.
An explicit choice survives subsequent upgrades, including preview-to-stable.
Daily checks are enabled initially. Download and installation require confirmation;
automatic-update offers are disabled. Sparkle supplies the standard release-note,
download, error, and Install and Relaunch UI.

Version-1 application preferences migrate to version 2 outside the archive.
Update fields are retained when recent archives change. No archive configuration,
OAuth credential, canonical message, manifest, or telemetry setting is changed.

## Native lifecycle

`sparkle.py` loads pinned Sparkle 2.10.0 and instantiates
`SPUStandardUpdaterController` through PyObjC on the main Cocoa thread. The
formal protocol supplies delegate method signatures; explicit block metadata
describes the deferred-install continuation. Native objects remain retained for
the application lifetime. A main-thread timer retries a deferred continuation
after application work finishes.

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
rewriting. Its release-run gate checks the embedded signature's presence;
cryptographic verification belongs to the release signer and native client.
Historical unsigned XML can be read as publication history before signing the
new complete feed; it cannot satisfy a new signed-feed release gate.

The private Sparkle key belongs in the protected release secret
`SPARKLE_ED25519_PRIVATE_KEY_BASE64`, with an offline recovery copy. Apple
Developer ID and notarization credentials remain separate. Before publishing,
allow `v*` tags in the github-pages environment. Do not change the deployed feed
URL or bundle identifier without an explicit migration.

For key loss/rotation, follow Sparkle's documented Developer ID signed-DMG
rotation path. Do not change Apple identity and Ed25519 identity together. A
replacement feed or missing private key is not evidence that existing clients
can accept the next release.

## Validation and remaining acceptance

`make test-updates` exercises migration/defaults, retained settings, ordered
versions, real writer exclusion, and checkpoint/definition deferral.
`make test-sparkle-signing` uses the actual pinned signer with a disposable key
and proves archive/feed tampering rejection and public-key matching.
`make sparkle-probe` builds a separate frozen fixture app and invokes its
delegate from compiled Objective-C, verifying track selection, BOOL return,
block ABI, and exactly-once continuation. Its unique defaults domain is removed
afterward. `make sparkle-probe ARGS=--feed-check` additionally performs a real
HTTPS preview check against the existing public feed and inspects Sparkle's
standard update window. It does not download or install an update.

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
