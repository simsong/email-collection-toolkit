<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Delivery implementation

Current implementation and validation boundaries. Deferred feature designs
live in [plans](../plans/README.md); historical evidence does not validate this revision.

## Build identity

`build_info` captures typed commit/dirty metadata from the owning Git checkout
once at import. It ignores inherited Git overrides and unrelated parent repos.
Embedded `build-info.json` takes precedence; missing/invalid provenance reports
Unavailable. About also captures the package version once, avoiding a later
editable install changing the identity of an already running process.
macOS staging and Windows private-runtime assembly write the metadata into the
package before signing. Ordinary unstamped wheels without Git cannot identify a
commit. `make test-build-identity` exercises disposable Git trees and the About UI.

`make gui` runs `scripts/run_gui.py`. On macOS it copies the small framework
Python executable into a fresh `.tmp/gui-launch-*/Email Collection Toolkit.app`,
with real bundle metadata. Ad-hoc signing and strict signature verification follow
metadata creation; an invalid bundle fails before launch. CPython's internal
`__PYVENV_LAUNCHER__` points to the active interpreter, with `PYTHONEXECUTABLE`
removed from the child environment. This preserves the original venv and worker
interpreter without placing unsealable venv files inside the app.
Exec preserves arguments and terminal streams;
unique bundles avoid overwriting a running executable. These development bundles
are disposable build artifacts, not standalone installers. The native lifecycle
test runs through that same bundle constructor and checks LaunchServices' actual
process name plus the About commit before exercising GUI behavior.
It also reads a randomly identified missing native credential. A separate
regression verifies the venv paths and rejects Info.plist tampering after sealing.

The development bundle has a different Keychain identity from the original
framework Python. Its IMAP password reads therefore invoke that existing
interpreter with `-I -m keyring`, explicitly selecting the macOS native backend
and password-only JSON output. Only account identifiers enter arguments; secrets
cross captured pipes, never a file or environment. The existing owned-command
supervisor bounds reads to 15 seconds and reaps children on timeout or owner exit;
subprocess failures and malformed private output produce sanitized errors without
retaining private validation exception chains. Original CLI and packaged apps
use their native backend directly. No vault ACL, password or default is changed.

## macOS packaging

`make dmg` builds the Python application described in
[MACOS_DISTRIBUTION.md](../MACOS_DISTRIBUTION.md). Experimental Rust UI packaging
and its helper have been removed; the historical snapshot is linked from the
[archive note](../RUST_GUI_EXPERIMENTS.md).

`make dmg-signed` calls `make dmg` with `--signing-identity` in `ARGS`, selecting
the first Developer ID Application certificate hash from
`security find-identity -v -p codesigning`. `SIGNING_IDENTITY` overrides this
default; an empty identity or `-` fails before building. `make list-signatures`
prints the same command's full list of valid code-signing identities. Existing
`ARGS` are retained, with the selected signing identity appended last.

`make ruff` passes a NUL-delimited list from `git ls-files --cached --others
--exclude-standard` to `uv run --locked ruff check --config pyproject.toml`. It is a required prerequisite
of `make check` and `make dmg`, and CI and source-release builds also run it.
Ruff retains its default error rules (`E4`, `E7`, `E9`, `F`), including unused
imports/variables and assigned lambdas; no per-file suppressions are
introduced. Pylint remains a complementary check.

`make syntax-check` uses standard-library `compileall` on `src`, `scripts`,
`tests`, and `e2e_tests`. It is a prerequisite of `make check` and `make dmg`;
the regression with a stray filename before the build script's opening
docstring fails this check before packaging starts.

The local `prepare-clamav` composite action installs hosted-runner build tools,
then invokes `make ci-clamav`. `scripts/ci_clamav.sh` verifies the upstream source
SHA-256 digests, builds OpenSSL 3.6.4 and ClamAV 1.5.4 under `.tmp/ci-clamav/`,
and explicitly selects that OpenSSL for CMake and Rust dependency discovery.
It preserves native licenses/certificates, checks actual dylib linkage and version,
and writes existing engine/updater/certificate overrides to `GITHUB_ENV` only after
validation. All four macOS build jobs use the same action. Ordinary source-test jobs
use `make freshclam`; installer jobs acquire definitions through staged ECT instead.
`make ci-clamav-check` selects the local runtime and exercises real definition
refresh, scanner and packaging tests; no verification bypass or daemon is introduced.

Both desktop entry points dispatch `--update-virus-definitions --directory` before
GUI imports. `clamav_update.download_definitions` uses the shared supervised updater,
temporary config and native clean/EICAR validator. A sibling hidden state directory
retains FreshClam cooldown state and its OS lock. Installer commands pass a stable
checkout-local `--state-directory` outside disposable build workspaces. Validated
files stage on the output filesystem and publish with exclusive native directory
rename, even when persistent state is on a different volume. Existing output, including
a directory created during the download, and user generations remain untouched.
The macOS builder freezes the runtime without definitions, seals it for launching,
runs its executable with host overrides removed, copies the verified export and
reseals before creating the DMG. It creates the normal resource/framework symlink
for the inserted data. Windows builds the private Python runtime once and invokes
its isolated `mailarchiver.desktop_entry` before copying definitions and running
MakeAppx. Its workflow runs source scanner tests against this packaged baseline
after assembly. Both candidate and tag workflows share these build paths.

`scripts/build_macos.py`, invoked by `make dmg`, uses project-local PyInstaller
dependencies, creates the app icon from the existing PNG, collects runtime
resources and dependency notices, declares `.mailarchive` document registration,
and signs the resulting bundle ad-hoc unless a signing identity was supplied
or both optional signing secrets are present. After PyInstaller collection, the
builder replaces its deduplicated OpenSSL pair with the two libraries named by
the installed ClamAV engine, rewrites `libssl` to load the adjacent `libcrypto`,
and signs both before resealing the app. This avoids an older Python-provided
`libssl.3.dylib` lacking symbols required by ClamAV; the mounted self-test
still checks actual native loading. The builder also re-signs `freshclam` as a
child executable, disabling hardened-runtime Team-ID validation for ad-hoc
development images. The mounted test launches its `--version` command with a
temporary configuration pointing at certificates in the mounted app, never the
host's `freshclam.conf`; this verifies that the updater and its bundled
libraries load. The runtime and development updaters use the same config writer.
The builder copies ClamAV's `COPYING.txt` and OpenSSL's `LICENSE.txt` from the
native library installations into `Third Party Notices`, along with the project
license and attribution files; the mounted test rejects missing or empty copies.
`scripts/macos_signing.py` imports
`APPLE_CERTIFICATE_P12_BASE64` using `APPLE_CERTIFICATE_PASSWORD` into a temporary
keychain, selects exactly one valid Developer ID Application identity, and
restores the original keychain search list and deletes the imported key in
`finally`. Native keychain errors omit secret-bearing command lines and output.
Passwords remain visible in `security` process arguments. Automatic import
requires the GitHub Actions hosted-runner environment; local signing uses an
explicit existing keychain identity. GitHub supplies `GITHUB_ACTIONS` and
`RUNNER_ENVIRONMENT` to every step as [default environment variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables#default-environment-variables);
neither the build nor the lifecycle test needs a workflow override. The workflow gates its secret-bearing step
on the hosted-runner context as well. These checks prevent accidental shared
runner use, not hostile code within the same job.
The builder signs and verifies the completed DMG before publishing the candidate.
Missing either secret emits `::warning::` and produces `*_UNSIGNED.dmg`; invalid
configured credentials fail. An explicit `--signing-identity` overrides secrets;
`-` emits a distinct warning identifying that deliberate unsigned override.
The release workflow builds the DMG on its hosted macOS runner, passes protected signing and
App Store Connect notarization credentials only to its packaging step, submits
the signed image through `make notarize-dmg`, staples and validates it, and then
retests the mounted artifact before assembling the source and DMG checksums into
a draft release. Missing credentials leave no publishable DMG and fail the
release job. Assembly checks out the Mac job's verified commit and checks that
the tag still names that commit. `notarytool` uses JSON output; a rejected
submission fetches Apple's issue log. Failures identify the signing, submission,
stapling, or Gatekeeper stage while redacting API-key values and the temporary
key path. The temporary `.p8` filename contains no API-key identifier, including
in setup-error paths. Gatekeeper assesses the disk image with Apple's
`context:primary-signature` context after stapling. Both jobs validate the
tag reference, checked-out commit, annotation, and project version before
installing dependencies. Unsigned annotated tags are accepted without a public-key
allowlist or GitHub signature verification. Repository permissions control
workflow changes and release-tag creation. `make test-signing` runs ordered focused lint/type checks and
regressions for credential selection, malformed input, identity ambiguity,
unsigned naming/warnings, sanitized errors, shared-runner rejection, and release
artifact ordering. A real Git fixture executes both workflow commit checks and
the tag/version validator: unsigned annotated tags pass; lightweight tags,
version mismatches, and a different checked-out commit fail.
Real Developer ID import/signing and hosted GUI execution require a credentialed
Mac release trial; pure policy tests do not establish those properties.
Ticket stapling/validation use at most three attempts only for stapler exit 68
with `NSURLErrorDomain Code=-1005`, retaining each command's timeout and sleeping
five then ten seconds. All permanent errors, exhausted retries and final trust
gates still fail. Real subprocess fixtures exercise recovery, bounds and rejection
without Apple credentials; hosted candidates exercise the actual Apple operations.

Release metadata is parsed with `packaging.version.Version`, rather than a
home-grown version regular expression. Package metadata and About use its
canonical spelling unchanged; Git adds only the `v` tag prefix. The supported
forms are stable `MAJOR.MINOR.PATCH`, alpha `MAJOR.MINOR.PATCHaN`, and beta
`MAJOR.MINOR.PATCHbN`; the parser rejects every other spelling or form. Alpha
and beta releases use Sparkle's `preview` channel and stable releases use the
default channel, with separate monotonically increasing numeric Sparkle build
values. The fixed website appcast starts empty; the Sparkle publication step
adds only a post-notarization, Ed25519-signed item.
`make sparkle-tools` pins Sparkle 2.10.0 and its upstream SHA-256, then places
the verified developer archive below `.tools/sparkle/`. Every invocation
compares cached `generate_keys` and `sign_update` bytes against the verified
archive before they can handle key material. `make sparkle-keys` calls Sparkle's
local `generate_keys`; the key generator retains the private
Ed25519 material in the developer's login Keychain and prints the public key.
The ordinary test suite skips the real-signer integration when these developer
tools are absent; `make test-sparkle-signing` installs them and requires that
integration test to run during release assembly.
The macOS bundle carries that public key and the fixed Pages appcast URL. The
appcast writer invokes `sign_update --ed-key-file -` on the final stapled DMG,
then invokes `sign_update --verify` on the same DMG and signature, passing the
protected exported Sparkle key only on standard input. Sparkle
2.10 explicitly supports this stdin form. New-format exported seeds decode to
32 bytes; legacy exports may decode to 64 bytes. Neither the key nor Apple's
credentials reach PyInstaller or mounted-app test subprocesses.
Branch CI installs the pinned Sparkle developer tools and runs the real signer
target. The tag run repeats that target and authenticates the previous published
feed before building the candidate DMG. `make release-appcast-base` shares the
exact feed-history fetch and validation between this preflight and assembly.
The release workflow requires the latest published release's `appcast.xml` asset
as its update-history base.
The one-time a10 feed signer passes an explicit historical tag to the mounted
test, which verifies the old app's version and embedded key and uses its actual
pre-Sparkle license notice set. The fixed mounted-test interpreter retains its
locked virtualenv symlink while scrubbing release credentials and path overrides;
resolving it to the host Python would lose the installed project. New DMG checks
still require Sparkle notices.
Only when the pushed tag is the repository's sole `v*` tag may it bootstrap
from the tracked empty seed; otherwise an empty release-list response fails.
It signs and prepends the new item, verifies the DMG and complete XML, and only
then creates a draft with every DMG, source artifact, checksum, and signed-feed
asset present. It publishes that complete draft, marking alpha and beta tags as
prereleases, then dispatches Pages on `main` with the exact release tag. The
standalone Pages workflow downloads that release's appcast; ordinary `main`
pushes select the latest published release. Both retry by exact tag and fail
if no published release or asset is available rather than publishing
the empty seed. It authenticates the downloaded XML before deployment.
Both triggers use this workflow and its queued concurrency group.
This keeps deployment within the Pages environment's `main` branch rule without
widening that rule. Neither workflow writes to protected `main`. A transient
deploy failure can be retried by dispatching Pages on `main` again.
Pages rejects any enclosure URL outside this repository's exact
tagged GitHub DMG downloads, including URLs on other HTTPS hosts.
Pages derives its stable and preview download links from published releases,
not all pushed tags, and recognizes canonical `aN` and `bN` preview suffixes.
Preview entries have Sparkle's `preview` channel and stable entries remain in
the default release channel.
`scripts/desktop_entry.py` dispatches normal GUI launch, `--cli`, `--self-test`,
and `--self-test-gui`. Frozen GUI resources use PyInstaller's bundle root;
the verifier's actual `.py` source is explicitly bundled for archive installation.
The Cocoa document delegate routes quit through the shared stop/checkpoint policy
and closes pywebview windows only after processing workers finish.

`scripts/dmg_layout.py` uses build-only `dmgbuild` to write the Finder `.DS_Store`
and background into the image without changing global Finder preferences.
AppKit draws a 2x-resolution TIFF at a logical 720-by-420-point size. The two
scales are linked by the bitmap's logical size; no additional drawing transform
is applied. `make test-packaging` renders the background and checks its logical
and pixel dimensions, ink bounds, and presence of title, arrow, and instructions.
The same pixel check runs before image creation and against the mounted image,
catching the double-scaling regression that metadata-only checks missed. The
real icons are 128 points, positioned app-left and Applications-right; the
background contains the title, arrow, and install/eject instructions.
Finder's outer window is 720 by 480 points, reserving 60 points for window
chrome so the background footer is not cropped if Finder shows its status bar.
The mounted build test decodes `.DS_Store` to verify layout metadata and
requires exactly the app and Applications as visible root items.
`make preview-dmg DMG=...` opens the mounted image in Finder for visual review
and ejects it when Return is pressed. This preview does not install the app.

`self_test.py` uses temporary source and archive fixtures plus isolated application
preferences. It verifies ingest, original bytes, FTS search, fixity, idempotence,
and not-scanned evidence. The visible mode exercises production window bridges
and cancels the real source picker after inspecting its warning banner.
After GUI shutdown, it joins non-daemon workers within one five-second budget.
Remaining workers produce a failed JSON report with their names, a Python thread
dump, and immediate nonzero test-process exit. The 120-second watchdog also
marks failure and dumps stacks. `make test-self-test` validates the worker gate
with real finishing, blocked and daemon threads in spawned child processes,
while an idle parent executor reproduces suite thread contamination. The parent
requires bounded successful child exit; the production worker gate is unchanged.
The target also runs the existing packaging tests.
The shared browser driver asserts Raw Source retention across message selection,
then explicitly selects HTML before exercising header-to-body find navigation.
The builder announces each mounted test, including its visible windows, and
requires the subprocess to exit successfully before accepting its report.
The DMG build stages the app, Applications symlink, and instructions, mounts
the compressed candidate read-only, verifies its seal, runs the frozen headless
test with a system-only PATH, and detaches in `finally`. `make check-release`
adds `--check-release` to enable the frozen GUI test; `DMG=path` adds `--test-dmg`
to validate an existing image. Ordinary builds and `make test-dmg` open no GUI
test windows. A normal rebuild removes any stale GUI report for its output path.
It publishes the candidate and requested JSON reports only on success.
The Mach-O audit reads library import load
commands explicitly, excluding `LC_ID_DYLIB`, which `otool -L` also displays.
A compiled-library regression proves that an alternate self install name
passes while a real unresolved import still fails. See [MACOS_DISTRIBUTION.md](../MACOS_DISTRIBUTION.md)
for commands, limitations, and Apple's renewal/notarization steps.

## Toolkit branding and website media

The application, installer, documentation, and website use Email Collection
Toolkit. The homepage Download button opens the GitHub releases listing.
Repository and Pages URLs use the renamed project. The platform
settings helper chooses the current application directory containing preferences.json or auth/, then an
existing legacy directory, without
moving or rewriting settings; Linux configuration,
package imports, CLI names, and archive-format identifiers retain compatibility.

`make website-screenshots` ingests purpose-made messages using the normal
archive service, then binds real Python services to the shipped HTML in
Chromium. Media targets explicitly select the dev dependency group, and search
capture waits for the rendered result-card count. It captures search and completed import history with no private mail.
`make website-gmail-illustrations` renders explicitly labeled setup diagrams
with placeholder account details. The homepage includes the unmodified
Wikimedia Commons hands/laptop SVG with visible CC BY-SA 4.0 attribution.
`searching.md` covers query syntax, suggestions, background results, attachment
limits, source filters, saved sets, and message viewing.

The macOS dependency audit reads actual dylib load commands from `otool -l`,
excluding `LC_ID_DYLIB`. A real compiled-library test verifies that an install
name alone is accepted while an executable's unresolved load of that same name
is rejected. This avoids rejecting the packaged pydantic-core library's own
identifier while retaining dependency checks.

Use the explicit `.mboxrd` derivative contract in [MBOX_READING.md](../MBOX_READING.md).

## Platform delivery references

Platform packaging and publication follow the [delivery references](../requirements/DELIVERY.md#platform-delivery-references).

### Sparkle application integration

`updates.py` holds typed updater status and exactly-once installation deferral.
`sparkle.py` retains a PyObjC standard controller, formal channel delegate, and
explicit block ABI metadata; Cocoa's main-thread timer resumes an idle reserved
installation. `gui_app.py` adds native menu/preferences controls and coordinates
quit, worker tails, and definition updates. `WriterLease` retains shared OS
locks in a private same-user temporary guard directory for every writer,
including CLI processes; definition refresh uses the same shared guard in both
GUI and CLI paths, retaining its separate definition-generation lock.
Installation retains an exclusive guard through resource
shutdown until process exit or failure. Ordinary shutdown cancels any reservation.
Writers on different archives remain concurrent. OS process death
releases guards. The archive's existing exclusive lock still controls its writes.
Raised continuation failures clear the update reservation and retain error state.
Canceled or failed deferred installation also aborts its pending Quit; the Quit
worker leaves windows open when cancellation wins or installation is underway.
`make test-updates` exercises both deferred cancellation and a raised continuation
after a real Quit request, then verifies definition work, archive creation and Quit retry.
Application preferences version 2 migrates update choices and preserves them
when recent archive paths change. `release_versions.py` supplies the build/appcast
mapper to runtime and release code; Cocoa uses its numeric build value.

`sparkle_bundle.py` preserves and signs Sparkle's framework/helpers and includes
the upstream notice file. `update_appcast.py` verifies the release key against
the app's public key, signs/verifies the stapled DMG, adds embedded notes/minimum
macOS metadata, and signs/verifies the complete XML. Publisher and signer-test
Makefile targets explicitly select packaging dependencies; ordinary test dependencies
also declare the cryptographic library directly. Release/Pages gates require
public-key verification of exact feed bytes and signed length on both main-push
and release-triggered deployment without rewriting signed bytes. The checker
accepts the pinned Sparkle signing-block format, bounds input to 16 MiB and uses
the embedded public Ed25519 key; its Makefile target provisions only pinned
pycryptodomex through `uv --no-project`, with no release secret or native signer.
Release assembly also verifies its previous published feed before appending or
re-signing history; only the sole-tag first-release seed bypasses that gate.
`make sign-historical-appcast RELEASE_TAG=v1.0.0a10 DMG=... APPCAST=... OUTPUT=...`
is the reviewed, local-only migration for the original a10 history. The Python
orchestrator verifies the DMG seal, stapled ticket, and Gatekeeper acceptance
before mounting it read-only. It verifies the app seal with nested-code checks
and app-level Gatekeeper acceptance before execution. The mounted app's
`SUPublicEDKey`, `CFBundleVersion`, and `CFBundleShortVersionString` must match
the signing key and reviewed a10 version before the private key is loaded.
The downloaded DMG is streamed into an owner-private, read-only temporary copy.
Image trust, mounting, Sparkle signature verification, and the internal
`make test-mounted-dmg` self-test use that single copy, whose digest is checked
again before output signing. The pinned Sparkle archive signature is checked
before the self-test exercises that same read-only mount. The migration invokes
the test script with the current absolute Python interpreter and a fixed
`PATH`, avoiding a caller-provided `make`; normal
`make test-dmg` still mounts its own image. The DMG attach/detach, app seal,
native dependency probes, and mounted executable tests share a filtered
environment without release credentials, Python/loader/archive overrides, or
Apple toolchain-selection variables. The signer receives
the Sparkle key on standard input while Apple credentials and those overrides
are excluded. `make update-appcast` sets the checkout root on `PYTHONPATH` so
its direct script entrypoint can import the shared signing filter. The migration
checks exact release metadata and protected-key
match before writing a separately signed XML feed. Its SHA-256 pin covers the complete
published feed bytes, including comments, whitespace, and XML declaration, so
parsing cannot erase lexical edits before the audit. The migration refuses an
existing output and does not upload or publish anything; an operator must
separately review and publish the resulting feed asset and Pages update.
External release notes are not supported because they need their own Sparkle
signature.
The publisher uses build-only `markdown-it-py` to render the changelog from its
first level-two entry heading. Both platform items embed the same HTML document,
with system fonts, light/dark colors and wrapping text. Raw HTML is disabled and
relative links resolve against the tagged `doc/RELEASE_NOTES.md` source. Previous
items retain their original descriptions. `make test-update-presentation` runs
ordered lint/types, signer/menu/update tests and browser rendering checks, saving
light/dark previews under `.tmp/update-notes-*.png`.
The frozen controller/compiled delegate probe and real HTTPS standard-UI probe
passed during development; these do not establish signed application replacement.
[SPARKLE_UPDATES.md](../SPARKLE_UPDATES.md) records validation targets and remaining
production acceptance, with macOS 15/arm64 as the configured initial target.

## Frozen acquisition modules

The macOS build derives hidden imports from trusted built-in acquisition manifests.
`make test-frozen-source-plugins` uses those same arguments and resources to build
a temporary PyInstaller CLI, ingest synthetic EML, and verify canonical bytes.
This component test does not establish signed/notarized DMG or Windows acceptance.
