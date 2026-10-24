<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Delivery requirements

Current subsystem contract and acceptance boundaries. This is not a claim of
platform/release acceptance. Deferred features live in [plans](../plans/README.md).

## Release policy

The agreed release policy in [DEVOPS.md](../DEVOPS.md) requires macOS-focused
ordinary CI, explicit cross-platform test releases, concurrent Mac/Windows
installer builds, coordinated publication through one signed appcast, and
static direct-download buttons derived from complete published releases. Reject
draft, absent, duplicate, incomplete-upload or wrong-URL installer metadata.
Preserve all paginated public releases before selecting a complete publication; keep
preview downloads distinct. The homepage always displays separate “Download Windows
installer” and “Download macOS installer” buttons beside “Show all installers”.
Each platform button links directly to its available published installer; a
missing asset falls back to the generic releases page with an availability
notice. All three buttons work without JavaScript or platform detection. The
page states Apple Silicon macOS and x64 Windows requirements, labels preview
downloads visibly, and exposes Windows certificate-trust instructions beside
the buttons before the additional-links disclosure. The website has no
update-stream selector; that preference belongs in the application.
These are release-workflow requirements, not evidence of Windows feature parity.
Website screenshot validation uses purpose-made synthetic mail and a real
browser: installed Edge on Windows, Playwright Chromium on other platforms.

## Windows development environment

Supported packages must bundle their selected executables and dependencies
without requiring user-installed runtimes, compilers or Outlook. The macOS
package includes the PST/OST helpers; Windows helper packaging and Linux
distribution remain deferred. Full installed ingest/scanner acceptance must be
established separately for each supported platform. The configured PST importer is the Rust helper;
Java is required only if a Java importer is selected for distribution.
[PST_IMPORTER.md](../PST_IMPORTER.md#platform-packaging-and-qualification) defines architecture-specific
packaging, runtime provenance, signing, confinement and installed-fixture gates.
No platform is supported merely because its package builds; validate full ingest,
scanning, locking, cancellation and recovery in the installed application.

[WINDOWS.md](../WINDOWS.md) documents building and running the x64 Python/WebView2
desktop and MSIX; its Rust/MSVC/Dioxus setup sections are historical. Setup must
distinguish installed tools from validated application support. Windows delivery
requires full ingest, preservation, recovery, and native desktop validation;
WSL/Linux results do not establish Windows compatibility. The Windows writer and
embedded scanner are implemented (see [Shared Python Windows desktop](DESKTOP.md#shared-python-windows-desktop));
PST/OST converters are not yet packaged for Windows.

## macOS desktop delivery

### Application updates (issue #91)

The Python Windows app uses WinSparkle with the shared release mapper, pinned
Ed25519 key, and user update preferences. Authenticate complete appcast bytes
before filtering to Windows x64 and package identity `ECT.PythonReader`; historical
Rust packages must never be offered. Compatible enclosures carry
`ect:packageIdentity` and `ect:architecture` in namespace
`https://simsong.github.io/email-collection-toolkit/updates`. Native WinSparkle
verifies the signed installer. Downloads and installation require user consent;
installation must reserve the common cross-process archive-writer guard.
Source launches allow manual discovery but must not run installers or automatic
checks. User settings and updater state are separate from archived content.

Both platform Preferences panels offer “Release only” and
“Alpha / beta / development and release”, using shared labels and the saved
`release`/`preview` application preference. Sparkle's allowed-channel delegate
and WinSparkle's authenticated feed filter honor that selection. Development
means published preview builds; it does not mean installing an arbitrary source
checkout. This preference is independent of website downloads.

The frozen macOS app must use Sparkle's standard updater UI, with native
Preferences… and Check for Updates… commands. Stable installations default to
release-only; alpha/beta installations default to preview plus stable. Explicit
choices and daily-check preferences migrate outside archives and survive recent
archive changes and upgrades. Download and installation require confirmation.
The updater must wait for all jobs, worker tails, definition replacement, and
writer leases and definition updates in the application and other same-user
CLI processes (including development definition refresh), then
atomically exclude new writers before relaunch. Resource shutdown must retain
the installation guard until process exit once relaunch has begun; ordinary
shutdown releases reservations. Failure, including a raised
native continuation error, must restore writer access and cancel any Quit waiting
for that update, allowing new work and a later Quit. Source launches without mapped updater metadata and unsupported platforms must
report updates unavailable without starting checks.

The app and publisher share numeric version mapping. Runtime Cocoa metadata
must not overwrite that build number with a package-version string. Bundle the
pinned framework with its helpers and full license notices, preserving links and
nested signing. Sign and verify both final DMG and complete XML; embedded release
notes and minimum macOS metadata are authenticated by the feed signature.
Reject a release private key that differs from the embedded public key.
Every alpha, beta, and stable release must derive its tag, channel and numeric
build from the project's canonical version. Branch CI and the tag run must
exercise the real Sparkle signer without a release secret. A tag run must verify
the published history before building a candidate, and sign and verify the
candidate DMG and complete XML before creating a draft release. A failed pushed
tag must remain immutable; a new code fix requires a new version and tag.
The audited a10 migration must validate the old mounted app against its actual
release version, embedded key and pre-Sparkle contents; candidate builds must
retain the full Sparkle notice and native checks.
Its mounted-test interpreter must retain the locked project's import path after
credential and loader-path overrides are scrubbed.
[SPARKLE_UPDATES.md](../SPARKLE_UPDATES.md) specifies behavior, operating procedure,
and the signed/notarized update acceptance that must precede completion of #91.

Ruff must pass with zero diagnostics before validation or packaging succeeds.
`make ruff` checks the repository using the locked development dependency;
`make check`, `make dmg`, and release builds must enforce it without ignoring
its exit status. Ruff is development tooling, not a bundled runtime dependency.

`make syntax-check` must compile all Python source, scripts, and tests without
executing them. Both `make check` and `make dmg` require this check so a syntax
error in a build-only script cannot escape ordinary validation.

`make dmg` builds a self-contained, native-architecture PyInstaller `.app` and
a compressed DMG containing it, an Applications shortcut, and drag-to-install
instructions.
`make dmg-signed` must invoke the same build with the first valid Developer ID
Application identity in the local Keychain search list, overridable with
`SIGNING_IDENTITY`. An absent identity or `-` must fail before building.

The experimental Rust UI and its packaging path are removed from the active
checkout and preserved at tag `experimental-rust-ui`. Supported packaging must
use the Python application.
`make list-signatures` must list valid local code-signing identities and their
certificate hashes. `make notarize-dmg DMG=...` submits an already Developer ID
signed DMG with protected App Store Connect API-key credentials, waits for
acceptance, staples the ticket, and requires Gatekeeper acceptance. It must not
accept an unsigned image or print credential material.
The Finder window must present a large app icon on the left and the real
Applications shortcut on the right, with an arrow and drag-to-install
instructions in the background. Only those two items are visible; instructions
must not require opening a separate text file. The mounted test verifies saved
icon positions, background, icon size, and absence of extra visible items.
The rendered Retina background must keep its title, arrow, and both instruction
lines within their intended regions without overlapping the icon locations or
clipping at the window edges. Validate rendered pixels as well as Finder metadata.

Python, native extension libraries, GUI assets, packaged schemas,
plug-in manifests, and the standalone verifier source travel inside the app.
The macOS app must bundle ClamAV, baseline definitions, the PST helper,
`mcti-scan` and the separate OST converter. Missing required scanner resources
must fail packaging. Tika/Java, general PDF OCR and Apple Intelligence are
outside the supported local-mail GUI runtime. A JVM is needed only if a Java
importer is deliberately added later.
When both `APPLE_CERTIFICATE_P12_BASE64` and `APPLE_CERTIFICATE_PASSWORD`
are present on an isolated GitHub-hosted runner, the build must import the
PKCS#12 identity temporarily, sign the app and DMG with Developer ID, verify
their seals, and remove the imported key.
If either secret is absent, continue with an ad-hoc-signed app and unsigned
DMG, emit a GitHub Actions `::warning::`, and append `_UNSIGNED` before `.dmg`.
Invalid configured credentials and signing failures must fail rather than
silently downgrade. Secret values must not appear in error output or artifacts;
restore the prior keychain search list on completion or failure. Explicit local
`--signing-identity` remains supported, including `-` to force unsigned output.
Explicit unsigned output must identify the override rather than report missing
credentials. Reject automatic PKCS#12 import on local and self-hosted runners:
`security` password arguments remain visible to other processes in the job.
Release builds triggered only by pushed `v*` tags must accept unsigned annotated tags without
configured release-signing public keys or GitHub signature verification. Before
installing project dependencies or using Apple secrets, require the tag to match
the project version and the checked-out commit, and require that commit to be
reachable from `main`. Lightweight tags must fail.
Administrators control release-tag creation and workflow changes through repository permissions.
Release assembly must include the tested, notarized DMG from the same commit as
the source archive and checksum the final image. Missing protected signing or
notarization credentials must fail release assembly; an unsigned development
DMG must never be published as a release. The archive extension is declared in
the bundle's document-type metadata.
The bundled ClamAV engine and updater must load against the matching OpenSSL
libraries from their ClamAV installation, not an older same-named library
selected from another Python dependency. The mounted DMG self-test must fail
when the native scanner or bundled `freshclam` updater cannot load. Both the
definition updater and mounted updater test must pass an explicit temporary
configuration, independent of any host ClamAV configuration.
Windows scanner workers must load their private native dependencies before
Python SSL imports, retain hard deadlines, and terminate with their owner.
Definition initialization must work without a bundled baseline. Writable
definitions and FreshClam state belong in the application's user data directory,
never an archive. Publication requires real clean and EICAR verdicts; the bounded
EICAR health sample may be scanned in memory to avoid host antivirus quarantine.
CDN cooldown state must survive failures; a blocked refresh cannot replace the
last validated generation. Explicit local seeding copies and validates definitions
without modifying the source or clearing the downloader's cooldown state.
Notarization failures must identify the failed stage and report Apple's
validation issues without printing API-key material.

Package metadata and the About window use one canonical PEP 440 version from
`pyproject.toml`; its annotated Git tag adds only `v`. Tests and workflows must
derive the current candidate from that metadata rather than pinning a mutable
release number.
The release parser rejects noncanonical or unsupported versions; only stable
`MAJOR.MINOR.PATCH`, alpha `MAJOR.MINOR.PATCHaN`, and beta
`MAJOR.MINOR.PATCHbN` are accepted. Alpha and beta items use Sparkle's
`preview` channel; stable items use its default channel. The published release
contains a signed `appcast.xml` asset; the tag workflow dispatches Pages from
`main` with the exact release tag after publication. Pages verifies and serves that
asset at the website's fixed HTTPS URL. Only a notarized DMG or the tested signed Windows MSIX bundle, each with a
verified Sparkle Ed25519 enclosure signature, may enter the feed. Authenticate
the complete mixed XML and preserve historical installer signatures.
The website's release links use published release tags, recognize the same
canonical alpha/beta spelling, and never advertise a draft or failed tag.
The homepage's primary Download action links to the GitHub releases listing.

`make sparkle-tools` downloads the pinned Sparkle developer archive to the
ignored project-local `.tools/` directory, verifies its SHA-256 before extraction,
and verifies cached `generate_keys` and `sign_update` bytes against that archive
before use. The ordinary test suite must not
require these optional developer tools; release assembly must run the real
signer test after installing them. `make sparkle-keys` invokes the
verified `generate_keys` tool locally. The private Ed25519 key remains outside
Git; it is never an application, Apple-signing, or notarization credential.
The packaged app contains only its `SUPublicEDKey` and fixed appcast HTTPS URL.
`SPARKLE_ED25519_PRIVATE_KEY_BASE64` is release-only: `sign_update` receives it
on standard input after Apple notarization/stapling, never through an argument,
bundle, or application subprocess environment. Before publication, the release
must have Sparkle verify the signature it generated against the final DMG
bytes; Pages cryptographically authenticates the XML against the application's
public Ed25519 key, checks the exact repository/tagged GitHub DMG
download URL and signature metadata, but does not re-download historical DMGs
to verify them. The signed DMG remains in a
draft GitHub release until the signed feed asset is attached; publishing the
draft is followed by a dependent Pages job using the signed feed produced in
that same release run. A Pages failure fails the release workflow. Ordinary
`main`-push Pages builds must fail if the published-release list or latest
appcast asset is unavailable; they must never deploy the tracked empty seed.
Both main-push and release-triggered Pages paths must verify the embedded feed
signature and its exact signed byte length before deploying an appcast. Feeds
are bounded to 16 MiB; forged signing markers, wrong keys and post-signing edits
must fail without rewriting authenticated bytes or requiring a private key.
The one-time migration for the first published release must operate on
downloaded copies, pin the complete known a10 feed by SHA-256 as well as its
release metadata and original DMG metadata, verify the
original Sparkle archive signature using the existing protected key, require
the mounted app's `SUPublicEDKey`, build number, and display version to match
the reviewed release, and authenticate the DMG's
Developer ID seal, stapled notarization ticket, and
Gatekeeper assessment. DMG trust checks must precede mounting; the app seal
(including nested code), app-level Gatekeeper acceptance, and embedded-key
check must precede executing any mounted binaries. A failed trust check or
self-test must stop signing. The migration must use one private read-only DMG
copy for image trust, mounting, Sparkle signature verification, and the mounted
app self-test; it must reject a copy changed during verification. The Sparkle
signature must authenticate the image before its app self-test. The migration
must invoke the mounted test through a fixed trusted interpreter and script,
without resolving its driver through caller-controlled `PATH`. Trust and test
subprocesses, including image attach/detach, app seal checks, and dependency
probes, must exclude release credentials and inherited Python/loader/archive
overrides. Native trust checks must also exclude Apple toolchain-selection
variables. Sparkle signer subprocesses must exclude Apple credentials and
those overrides. It must verify the cached Sparkle signer against each
required member of the pinned archive, checking extraction status before
comparison. It must sign a distinct
appcast output with the existing Sparkle key, verify the resulting XML
signature with the embedded public key,
and refuse to overwrite either source or an existing output. It must not upload,
replace, or publish a GitHub release asset or Pages site; those remain separate
reviewed operations.
Release assembly may bootstrap from the tracked seed only when the pushed tag
is the repository's sole `v*` tag; otherwise it must fail rather than reset
update history when its previous published feed cannot be obtained. Any previous
published feed must authenticate against the embedded public key before release
assembly appends or re-signs history. Unsigned legacy history requires a separate,
reviewed migration. Neither
workflow may write directly to protected `main`.

Ordinary `make dmg`, `make dmg-signed`, and `make test-dmg` must run only the
headless mounted self-test, without opening GUI test windows. `make check-release`
must additionally run the visible native self-test on the built DMG; `DMG=path`
selects an existing image instead of rebuilding. GitHub release assembly must
require headless `make dmg` validation before uploading the DMG.
Both paths must mount read-only, verify the bundle seal, and detach the volume
even on test failure. Tests use disposable fixtures and
preferences, never the last real archive. They exercise no-ClamAV ingest,
source-byte preservation, search, BagIt verification, repeat-import idempotence,
native bridge startup, and the missing-antivirus banner. A failed check prevents
replacement of a prior DMG. JSON reports accompany the successful artifact.
Announce the visible test before opening its windows. GUI test success requires
background workers that block process exit to stop within a five-second grace
period after GUI shutdown. A remaining worker must produce a failed report,
thread stacks, and a nonzero exit rather than a successful report followed by
an interpreter-shutdown hang. The parent must still require successful process exit.
Worker-gate regression tests must run in fresh processes so unrelated test-suite
executors cannot contaminate the desktop shutdown result; they must retain real
finishing/blocked-worker assertions and bounded child exit.
The bundled-library audit must distinguish `LC_ID_DYLIB` metadata from actual
load commands; a binary's own install name is not an imported dependency.
Unresolved imported libraries must still fail validation.

## Copyright and redistribution

* Every project-owned, comment-safe source, script, test, configuration,
  template, and documentation file carries `Copyright (C) 2026 Simson L.
  Garfinkel. All Rights Reserved.` using its native comment syntax. Shebangs,
  encoding declarations, XML declarations, and HTML doctypes remain first.
  Missing notices are reported as warnings with a successful checker exit status;
  they must not fail CI or release builds.
* Canonical mail, test fixtures, datasets, generated files, binaries, lockfiles,
  minified files, vendored trees, standard interchange formats whose semantics
  a comment could change, and files with another copyright or license are not
  rewritten to add the project notice. This includes upstream website artwork
  and generated shared-workflow files; project-authored type stubs and JavaScript
  modules remain eligible.
* Existing contributor, copyright, and license notices are preserved. The
  repository `COPYRIGHT` file limits the project claim to material for which
  the named owner holds copyright; `THIRD_PARTY_NOTICES.md` identifies vendored
  and separately licensed material. All project-owned code, tools, documentation,
  and the website theme use GPL-2.0-only, with additional licenses available
  from the copyright holder. The independent libpff converter is GPL-3.0-only
  as a compatible license for our converter code. Upstream pypff/libpff remains
  LGPL-3.0-or-later; no upstream relicensing is required. GPLv2-only and
  GPLv2-or-later have different linking compatibility; see the
  [licensing explanation](../../THIRD_PARTY_NOTICES.md#gplv2-and-lgplv3-compatibility).
  Third-party license grants remain unchanged.
* A source or binary distribution includes `LICENSE`, `COPYRIGHT`,
  `THIRD_PARTY_NOTICES.md`, and every license text required by its included
  components. The macOS DMG includes the ClamAV and OpenSSL license texts from
  the same installations as its native libraries and verifies them mounted.
  Each platform's binary build audits its exact runtime dependency
  closure and fails for unknown licenses, missing license texts, or
  development/test packages. Dependency licenses and required notices are
  retained. A passing inventory audit is not license compatibility clearance;
  the remaining Apache-2.0 ftfy compatibility issue is
  recorded in THIRD_PARTY_NOTICES.md.
* Copyright ownership and redistribution terms require owner or counsel review
  before public binary release; automated checks are inventory controls, not
  legal advice.

## Toolkit identity and website illustrations

All public product text uses Email Collection Toolkit, and repository/Pages
links use `simsong/email-collection-toolkit`. Existing macOS/Windows preferences
and OAuth directories remain readable under their prior directory name; new
installations use the current product name. A file, empty directory, or directory containing only incidental metadata at the new path
must not hide an existing legacy settings directory. No source mailbox or canonical
archive is renamed. Python package and CLI identifiers remain compatible.

The homepage links Collect to Importing, Search to Searching, and preservation
formats to their specifications, with an ePADD project link. Searching documents
the implemented query forms and viewer controls, with planned features labeled.
Homepage and Searching share a real synthetic-data search capture; Importing
shows the real import-history interface. Interface changes require screenshot
regeneration through the Makefile. External clipart has visible author, source,
and license attribution. Gmail setup diagrams are labeled as illustrations,
not screenshots of a live third-party account.

The macOS bundle dependency audit shall distinguish a Mach-O library's own
`LC_ID_DYLIB` from actual dylib load commands. Its own install name need not
resolve as another bundled file; actual non-system dependencies must resolve
inside the app. A compiled-library regression shall exercise both cases.

Use the explicit `.mboxrd` derivative contract in [MBOX_READING.md](../MBOX_READING.md).

## Platform delivery references

[WINDOWS_MSIX_TEST.md](../WINDOWS_MSIX_TEST.md) owns current x64 Python package
structure and installed acceptance. [WINDOWS_PUBLIC_SIGNING.md](../WINDOWS_PUBLIC_SIGNING.md)
owns public trust, publisher identity, OIDC provisioning and verification.
[MACOS_DISTRIBUTION.md](../MACOS_DISTRIBUTION.md) owns DMG/signing acceptance, and
[DEVOPS.md](../DEVOPS.md) owns coordinated publication. Historical Rust/MSIX
chronology is consolidated in [RUST_GUI_EXPERIMENTS.md](../RUST_GUI_EXPERIMENTS.md);
it does not establish current-head packaging or Windows feature parity.

## Frozen acquisition modules

Every bundled source/file manifest must resolve in the frozen application, even
when that provider is unused. Synthetic local ingest must load the registry and
preserve canonical bytes without an installed source checkout.
