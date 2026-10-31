<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# For developers

The [project README](../README.md) is for application users. This guide covers
running a checkout, building installers, and testing. The
[documentation guide](README.md) links to architecture, preservation contracts,
platform requirements, and release procedures.

## Source installation

Use Python 3.12 or newer and the project's locked uv environment. The installed
desktop application already contains its runtime; these steps are for a checkout.

Install [uv](https://docs.astral.sh/uv/) and a ClamAV installation that
provides `libclamav` (for example, Homebrew `clamav` on macOS), then prepare
this project and its local virus definitions:

```console
cd email-collection-toolkit
uv sync --locked
make freshclam
```

`make freshclam` seeds and updates `etc/clamdb` from the installed database.
Before ordinary Python scan sessions, daily definitions over 24 hours old are
automatically refreshed when the update site is reachable. Offline or failed
updates retain the existing definitions; age alone does not prevent scanning.
Explicit `MAILARCHIVER_CLAMAV_DATABASE` overrides remain pinned without automatic
updates. `make test-definition-refresh` validates this policy with local fixtures.
No ClamAV daemon is configured or started; see
[embedded antivirus](EMBEDDED_CLAMAV.md). Run CLI workflows through `make run ARGS=...`.

Set the archive directory once for the shell session:

```console
export MAIL_ARCHIVE_DIR=/path/to/mail-archive
```

Local ingest and search use this value. Pass `--archive DIRECTORY` before the
command to override it for one invocation; IMAP requires an explicit archive.

For development PST/OST imports, build the helpers with
`make pst-importer mcti-scan pff-converter`. Rust/Cargo and the converter build
prerequisites are described in [PST/OST importers](PST_IMPORTER.md).

Launch the desktop from the checkout with `make gui`. Use Makefile targets for
ordinary run and test workflows; command syntax is in the
[CLI reference](CLI_REFERENCE.md). Never use a personal archive for development
or tests without explicit authorization; use synthetic fixtures or copied data.

## Desktop implementation

The desktop uses Python/pywebview on macOS and Windows. Windows uses WebView2
and a private x64 CPython runtime. The macOS package bundles the Rust PST
importer, `mcti-scan`, and the separate `pff-converter`; Windows packaging of
these importer tools remains unfinished.

On macOS, `make gui` creates an ad-hoc-signed development application under
`.tmp/` that reuses the active virtual environment. The launcher preserves the
original interpreter's native credential access without changing Keychain
policies. Packaged applications have their own native-vault identity.
See [delivery implementation](implementation/DELIVERY.md) for the launch and
build-provenance boundaries.

The experimental Rust UI is archived outside the active source tree; see
[Rust UI archive](RUST_GUI_EXPERIMENTS.md). Independent Rust importers, verifiers,
and MIME tools remain supported. The pinned compiler is selected by
`rust-toolchain.toml`; `RUSTUP_TOOLCHAIN` can override it.

## Rust importers

Rust importer development uses `make rust-programs` to build `mdti-validator`,
`mcti-generator`, and [`pst-importer`](PST_IMPORTER.md). See [MCT Importer API 1.0](MCT_IMPORTER_API.md) for
the contract, individual targets and validation pipeline. Rust/Cargo are
required to build these tools. The standalone PST helper uses Microsoft's Rust crate;
the CLI archive host runs it through the common processing pipelines. The macOS
DMG bundles `pst-importer`, `mcti-scan` and the `pff-converter`, so packaged
users need no compiler; the Windows MSIX does not yet include them.

PST test inventories and the Rust corpus downloader live in [`pst/`](../pst/README.md);
use `make pst-download-plan` to inspect the plan and `make pst-download` to fetch
into ignored `var/pst/`.

## Apache Tika preparation

Apache Tika is **not used by the program yet**. The Makefile can download,
verify, and unpack its command-line application distribution in preparation
for opt-in extraction from PDF and Office attachments, but no current ingest
or search path invokes it.
Normal mail ingest and body-only search do not need Java. The current
`--index-attachments` option indexes text attachments only.

Tika requires Java 17 or newer. The following downloads Tika 4's ZIP
distribution, verifies Apache's published SHA-512 checksum, and unpacks the
application JAR with its required `lib/` directory below this checkout's
ignored `.tools/tika/4.0.0/` directory. It does not install a service or
schedule any work:

```console
make install-mac
# or, on Linux:
make install-linux
```

The installer uses `TIKA_VERSION=4.0.0`. To install a later Apache release,
set that variable after checking its release notes and checksum:

```console
make install-mac TIKA_VERSION=X.Y.Z
```

These targets install only the optional Tika tool, not Email Collection Toolkit.
Installing Tika does not enable PDF/Office indexing: that integration remains
future work. See the [search requirements](requirements/SEARCH.md).

## Native antivirus runtime

macOS CI and release jobs use `make ci-clamav` to build checksum-pinned ClamAV
and OpenSSL below `.tmp/ci-clamav/`. The shared action exports verified engine,
updater, and certificate paths through `GITHUB_ENV`. With prerequisites already
installed, `make ci-clamav-check` refreshes definitions and runs real scanner and
packaging checks against this runtime. Windows uses its separately pinned
portable runtime.

Installer builders run staged ECT's explicit-directory definition downloader
before packaging and final signing. Both platforms require network access for
this step; neither downloads definitions through the source checkout. No
resident antivirus service is configured.

## Tests

`make test-ingest-progress` checks source versus saved-mail progress, independent
worker task totals, and the actual browser display using synthetic archives.

The full test architecture and its explicit browser/Cocoa coverage boundary are
documented in [`doc/END_TO_END_TESTING.md`](END_TO_END_TESTING.md).

The ordinary suite uses static MBOX and `.emlx` fixtures. Antivirus tests build
the EICAR signature from fragments only inside a disposable test directory,
ingest it with the real embedded ClamAV engine, and immediately delete the
generated source; no complete virus-test signature is tracked in Git. The
separate end-to-end suite copies its tracked, virus-free source corpus, ingests
210 observations with Rust-owned lifecycle assertions, and verifies deduplication,
autosave exclusion, quarantine,
newline preservation, attachment indexing, BagIt fixity, and the installed
standalone verifier. Headless Chromium drives the shipped HTML and JavaScript
through the real Python service bridge, including empty-query suppression, complete searches,
sorting, message and MIME views, provenance, remote-content blocking, previews,
exports, printing, drag-out, keyboard navigation, and error display:

```console
make test
make test-e2e
make check
```

Install the pinned headless Chromium once with `make install-test-browser`.
`make check` runs Ruff and Pylint, then ty and Pyright, then Rust and license
checks, then both test suites and website validation without showing a window.
CI runs `make check-static` and `make check-tests` in parallel jobs; local
`make check` retains their order. Use `make lint` and `make types` for the
static checks alone. On macOS,
`make test-quit` checks real immediate/bounded process exit, message-boundary
stopping for local and loopback API sources, and interrupted archive recovery.
`make test-native-reprocessing` uses the same `MAILARCHIVER_NATIVE_APPLICATION_E2E=1`
opt-in as the application lifecycle check. It tests real menu confirmation and
Cancel against disposable canonical mail; it never uses a personal archive.
`make test-native-worker-preferences` uses that same opt-in for the real modeless
Preferences window, traffic-light Close, saved edits and CLI-locked worker controls.
Its windows appear on the leftmost attached display.

`make test-native-quit` uses `MAILARCHIVER_NATIVE_GUI_E2E=1` to exercise Quit
during status polling in a real macOS Cocoa window; it requires a GUI session.
Idle Quit exits after bounded private-export cleanup. Active imports finish their
current message and checkpoint; imports, settings saves, definition updates, and cleanup share one
five-second deadline. Forced exit can leave stale manifests or an
incomplete append; Continue Processing or reimport invokes archive recovery.
Saved scanner failures are rescanned on retry. Normal runs with unlimited content
processing restore archived content before pending ingestion, keeping existing mail
searchable even if a pending import fails. To rebuild only the disposable search
index without resuming ingestion, use `make run ARGS="--archive /path/to/archive refresh-index"`
(add `--index-attachments` when wanted). `make test-ingest-recovery` validates these paths.

`make test-native-gui` additionally exercises the hidden Cocoa/WKWebView bridge.
This native target is an explicit local development check and does not run in
CI/CD, which retains the complete headless Chromium GUI test.
`make test-native-html-find` is a separate visible, opt-in macOS check for
WKWebView HTML finder highlighting and scrolling.
Regenerate the committed safe corpus after an
intentional fixture change with `make fixture-e2e`.

Public-corpus validation runs locally or on explicitly provisioned ephemeral EC2
workers. See [regression policy](REGRESSION.md) and
[validation setup](../validation/README.md). Deployment and corpus runs are
separate actions from local tests.

## macOS packaging

`make dmg` (also `make python-dmg`) packages the Python runtime, GUI, plugins,
schemas, importer tools, ClamAV engine/definitions, and Sparkle. The target
requires the native build prerequisites, mounts its output DMG, and runs
headless synthetic acceptance. `make test-dmg DMG=...` repeats that acceptance;
visible native validation is separate (`make check-release`). Building does not
install or publish the application.

Use `SIGNING_IDENTITY` for a local Developer ID identity. Hosted signing and
notarization credentials are described in the README's
[environment-variable reference](../README.md#environment-variables), with
renewal and platform details in [macOS distribution](MACOS_DISTRIBUTION.md) and
[certificate management](CERTIFICATE_MANAGEMENT.md).

## Windows development

Windows runs the same Python/pywebview application as macOS, using WebView2.
`make gui` runs it from a checkout; `make test-python-reader` and
`make test-python-reader-native` validate synthetic reading/export and the
native bridge. Windows New/Import uses the common Python engine with native
locks and byte-preserving MBOX I/O. See [the Windows Python desktop
notes](PYTHON_WEBVIEW.md) for validation and remaining gaps.

`make prepare-windows-clamav` explicitly downloads the pinned official portable
x64 runtime into `.tmp/clamav-x64`; it installs no service. `make clamav-update`
fetches and validates definitions in application-specific user storage, outside
archives. `make clamav-seed SEED_DIRECTORY=...` copies an existing baseline and
validates it without modifying the source or clearing CDN cooldown state.
`make freshclam` prepares `etc/clamdb` for development tests. Installer builders
stage the native runtime and ECT once, run that staged ECT's
`--update-virus-definitions --directory NEW_DIRECTORY` command, and include the
verified output before final DMG signing or MSIX assembly. Both installer builds
require network access; neither downloads through the source checkout.
Existing `MAILARCHIVER_CLAMAV_LIBRARY`,
`MAILARCHIVER_FRESHCLAM`, `MAILARCHIVER_CLAMAV_DATABASE`,
`MAILARCHIVER_CLAMAV_CERTIFICATES`, and `MAILARCHIVER_CLAMAV_UPDATES` override
the engine, updater, baseline databases, signing-certificate directory, and
per-user definition-update location respectively.

`make prepare-windows-updater` prepares the pinned WinSparkle DLL and licenses;
the MSIX includes them. `make test-windows-scanner` and `make test-windows-updater`
exercise the native runtime and update filtering. Help offers update checks and
settings. Source launches support manual discovery only. The shared feed must
publish a signed Python Windows enclosure with the package identity documented
in the requirements before an installed Python upgrade can be offered.

`make msix-test` builds the Windows payload; `make test-msix MSIX_PACKAGE=...
MSIX_EVIDENCE=...` tests relocation. `make msix-bundle MSIX_PAYLOADS=...` signs the
bundle using `MSIX_TEST_CERT_PFX_BASE64`, the private key matching the existing
public test certificate. Never publish that variable's value. `UV_CACHE_DIR`,
`UV_PYTHON_INSTALL_DIR`, and `UV_PYTHON_BIN_DIR` may place uv's cache, managed
interpreters, and interpreter links inside a development checkout. The packaging
script temporarily sets `UV_PROJECT_ENVIRONMENT` to its isolated runtime venv.
The focused lint target sets `PYTHONPATH` to the checkout solely to resolve
repository-local test helpers; the packaged interpreter remains isolated.

## Windows MSIX packaging

See [Windows MSIX test packaging](WINDOWS_MSIX_TEST.md) for automated build/sign/test
commands, external WebView2 detection, and native Windows evidence. Windows
packages target x64. Public release and test-signing paths are distinct;
installed feature acceptance must be checked independently of package signing.

For direct Cargo commands, `CARGO_TARGET_DIR` selects a reusable build cache;
for Make targets, use `RUST_TARGET_DIR` instead.

Windows test signing uses the GitHub Actions secret/environment variable
`MSIX_TEST_CERT_PFX_BASE64`, containing the base64-encoded persistent test PFX.
Only the signing step receives it. Local signing requires the same variable;
missing or mismatched keys fail rather than creating a new certificate. The
public identity is `scripts/win/test-signing.cer`; private keys never belong in
Git or artifacts. Testers trust it once, until expiration or deliberate rotation.

Release and candidate workflows require Microsoft public signing. See
[Windows public signing](WINDOWS_PUBLIC_SIGNING.md) for provisioning and checks.
Repository variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
`AZURE_SIGNING_ENDPOINT`, `AZURE_SIGNING_ACCOUNT`, and `AZURE_SIGNING_PROFILE`
select the OIDC identity and signing service. `MSIX_PUBLISHER` is the exact approved
certificate subject, used in the manifest and verification. `PUBLIC_SIGNING` is
the workflow's boolean mode selector. Missing production configuration fails;
release bundles never fall back to the test key. `make test-release-files`
validates staging; `make verify-public-msix MSIX_PUBLISHER=...` verifies Windows
trust, timestamps and publisher on Windows after signing.

## Versions, updates, and release workflow

`pyproject.toml` owns the canonical PEP 440 version. The shared
`release_versions.py` mapper derives tags, update channels, and monotonically
ordered Sparkle build numbers. It accepts stable `MAJOR.MINOR.PATCH` and numbered
`aN`/`bN` previews. Do not hard-code candidate versions or build numbers.

User-visible application fixes handed back for testing advance the version and
`uv.lock`; alpha/beta fixes advance their prerelease number. About snapshots the
launch/build commit and discloses local changes. Platform builders embed this
provenance before signing.

Both platforms use saved update preferences: Sparkle on macOS and WinSparkle on
Windows. Source launches allow manual discovery but never execute downloaded
installers. A complete updater-driven Windows upgrade remains unvalidated.
See [Sparkle updates](SPARKLE_UPDATES.md) for lifecycle and acceptance boundaries.

`[release-ci]` opts a branch into signed/notarized DMG and Windows installation
checks and installer/feed signing. `make test-python-desktop-package` checks
entry points and packaging gates. `make release-candidate-base APPCAST=...`
authenticates published history; `make release-candidate-feed APPCAST=...`
signs and verifies a candidate without creating a release or tag.

Before publication, branch CI must exercise the real Makefile signing and
release gates. The release workflow verifies prior published history, builds
both installers, authenticates their signatures and the complete appcast, and
only then creates and publishes a complete release. A pushed tag is immutable;
fixes require a new version and tag. Publication requires separate authorization.
See [DevOps](DEVOPS.md#github-actions) for exact triggers and gates.

Pages deploys from `main`, resolving published release metadata and verifying
the signed feed. A merge and a release dispatch can each trigger deployment;
check the actual workflow before adding another run. For download-page
acceptance, use `make website-download-check`.

Release builds must run `make runtime-license-check` on every platform and
include `make runtime-license-bundle LICENSE_OUTPUT=PATH` output.

## Environment and research settings

The [README environment-variable reference](../README.md#environment-variables)
documents runtime, build, signing, test, and research overrides without secret
values. Keep new settings documented there and beside their Makefile targets.

Name-matching experiments use the separate locked environment in
[`research/name-matcher`](../research/name-matcher). See the
[research notebook](research/name-matcher/README.md) for methods, commands,
validation limits, and private-output handling.
