<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Developer commands

These commands build, test, package, or diagnose a source checkout. For installed
application commands, use the [CLI reference](CLI_REFERENCE.md). Start with
[For developers](FOR_DEVELOPERS.md) for prerequisites; each specialist guide owns
its operational and acceptance limits.

## Research and diagnostic commands

These tools create private derivatives and require explicitly selected inputs
and outputs. They do not modify source stores or replace canonical messages.

| Developer target | Purpose |
| --- | --- |
| `make data-quality-audit ARCHIVE=PATH EARLY_SOURCE=PATH AUDIT_OUTPUT=NEW_DIR` | Export verified early-message and missing-sender evidence |
| `make data-quality-babyl-audit EARLY_SOURCE=PATH AUDIT_OUTPUT=DIR` | Inspect Babyl source records |
| `make data-quality-summary AUDIT_OUTPUT=DIR` | Summarize exported evidence |
| `make name-matcher-observations ARCHIVE=PATH OUTPUT=NEW.sqlite3` | Export identity/signature evidence |
| `make addressbook-export ARGS='--archive PATH --output NEW.txt'` | Experimental ePADD replacement address book |

`ect h3-review --help` describes the installed semantic-hash review exporter.
See [Apple Mail comparison](APPLE_MAIL_CACHE.md), [audit tooling](DATA_QUALITY_AUDIT.md)
and the [research notebook](research/name-matcher/README.md) for interpretation.

## Rust tools

`make rust-programs` builds the supported workspace release binaries in
`target/release/` (`RUST_TARGET_DIR` overrides it). Build a single tool with
`make TOOL_NAME`. Runtime syntax:

| Binary | Interface |
| --- | --- |
| `archive-verifier` | `ARCHIVE`: read-only catalog/MBOX/search consistency; stop writers first |
| `mcti-generator` | `COUNT`: synthetic API mboxrd to stdout; zero emits an empty stream |
| `mdti-validator` | `[--max-message-bytes N]`: validate stdin, discard input, nonzero on failure |
| `mcti-scan` | `INPUT.mboxrd`: scan reconstructed messages and emit API stream |
| `pst-importer` | `[--only-invalid] [--offset N] [--limit N] [--] FILE`: read-only PST extraction to stdout |
| `pst-downloader` | Corpus planning/downloading; use wrappers below and [pst/README.md](../pst/README.md) for manifest/retry details |

Generator, validator and PST importer accept `--api-version` / `--version`;
`--help` is supported by those tools and archive-verifier. `mcti-scan` requires
an input file rather than `--help`. Extraction diagnostics go to stderr; nonzero
status can mean partial results, which must be retained and reported.

PST downloader options: `--inventory-dir DIR` (default `pst`), `--output DIR`
(default `var/pst`), `--scope fixtures|archives|all` (default all), `--dry-run`,
`--limit N` source URLs, `--max-download-bytes N` (default 16 GiB),
`--max-expanded-bytes N` (default 64 GiB) and `--timeout-seconds N` (default 1800).
`PST_DOWNLOAD_ARGS` passes these options through both acquisition wrappers.

The separate Python `pff-converter FILE` accepts `--receipt FILE`,
`--diagnostics FILE`, `--timeout-seconds N` (default 60),
`--max-message-bytes N` (default 64 MiB), `--max-folder-depth N` (default 64),
`--offset N` (default 0) and `--limit N`. Build through `make pff-converter`;
its dependencies live in a separate environment and are not imported by the host.
See [PST_IMPORTER.md](PST_IMPORTER.md) for OST scope and stream composition.

`make verify-database ARCHIVE=ARCHIVE` builds/runs the independent Rust verifier.
`make pst-import PST=FILE ARGS='OPTIONS'` runs the importer;
`make pst-download-plan` inspects acquisition and `make pst-download` downloads
into ignored `var/pst/`. PST-specific pipeline examples are intentionally kept
in [PST_IMPORTER.md](PST_IMPORTER.md) and [MCT_IMPORTER_API.md](MCT_IMPORTER_API.md).
The supported MIME decoder is a library, not another CLI.
The Rust GUI is retired; its old commands are history in
[RUST_GUI_EXPERIMENTS.md](RUST_GUI_EXPERIMENTS.md).

## Windows packaging

These targets run on a Windows development/CI host; outputs are local artifacts
unless a workflow explicitly publishes them.

| Target | Parameters / purpose |
| --- | --- |
| `prepare-windows-clamav` | Stage verified x64 runtime and baseline definitions |
| `prepare-windows-updater` | Stage pinned WinSparkle SDK and notices |
| `msix-test` | `MSIX_ARGS='-Architecture x64'`: build Python payload |
| `test-msix` | `MSIX_PACKAGE=PATH MSIX_EVIDENCE=NEW_DIR`: validate extracted payload |
| `msix-bundle` | `MSIX_PAYLOADS=DIR MSIX_ARGS='OPTIONS'`: assemble/sign bundle; `-PublicSigning` prepares Microsoft-action input |
| `verify-public-msix` | `MSIX_PUBLISHER='approved subject'`: validate public trust/timestamp/identity |

[WINDOWS_MSIX_TEST.md](WINDOWS_MSIX_TEST.md) owns package/trust boundaries;
[WINDOWS_PUBLIC_SIGNING.md](WINDOWS_PUBLIC_SIGNING.md) owns Azure provisioning.

## Build, release and maintenance entry points

Makefile variables are parameters to targets, not automatically process
environment variables. The root [README](../README.md) documents environment
variable names and purposes; no secret values belong in documentation.

| Target | Purpose / parameters |
| --- | --- |
| `sync-dependencies` | Synchronize Python environment |
| `build-sdist` | Source distribution; `DIST_DIR` output |
| `gui` | Launch supported desktop; `ARGS` passes its options |
| `dmg`, `python-dmg` | Build Python macOS application/DMG; `ARGS`, optional `SIGNING_IDENTITY` |
| `dmg-signed`, `list-signatures` | Build with Developer ID / list available identities |
| `notarize-dmg` | Submit/staple local signed DMG; `DMG`, `ARGS` |
| `check-release`, `test-dmg`, `test-mounted-dmg` | Candidate and mounted-artifact acceptance; see distribution guide |
| `preview-dmg` | Explicit preview package, not publication |
| `sparkle-tools`, `sparkle-keys` | Verify/stage pinned tooling / key operation |
| `update-appcast`, `check-appcast` | Construct/validate selected feed; `RELEASE_TAG`, `DMG`, `APPCAST`, `ARGS` |
| `release-files`, `release-tag-check` | Stage validated assets / enforce metadata-matching immutable tag |
| `release-candidate-base`, `release-candidate-feed`, `release-appcast-base` | Shared candidate/feed preparation gates |
| `release-package-check` | Package acceptance prerequisite |
| `sign-historical-appcast` | One-time reviewed historical migration; requires explicit pinned historical inputs |
| `freshclam` | Seed/update checkout definitions in `etc/clamdb` |
| `clamav-update` | Refresh application per-user definition generation |
| `clamav-seed` | Explicit baseline preparation; consult Makefile parameters |
| `website-check`, `website-build-check`, `website-download-check` | Asset/link, built-site and rendered download acceptance |
| `website-preview` | Build/open local site preview |
| `website-release-data` | Derive installer metadata; `RELEASES_JSON` |
| `website-screenshots`, `website-preview-screenshots` | Synthetic interface/site screenshots |
| `website-icons`, `website-gmail-illustrations` | Developer illustration generation |
| `install-mac`, `install-linux`, `install-test-browser`, `install-tika` | Explicit developer prerequisite installation; not ordinary application startup |
| `ocr-inventory`, `ocr-profile`, `ocr-analyze`, `ocr-run`, `ocr-experiment` | Experimental OCR fixture pipeline; see PDF strategy |
| `check`, `check-static`, `check-tests` | Ordered full/static/test verification |
| `ruff`, `pylint`, `ty`, `pyright`, `lint`, `types`, `syntax-check` | Individual static checks |
| `rust-check`, `rust-fmt`, `rust-lock`, `rust-toolchain` | Workspace validation/format, dependency update, selected compiler |
| `runtime-license-check`, `runtime-license-bundle`, `copyright-check`, `distribution-check` | Distribution/license/source audits |

The remaining `test-*`, `fixture-*`, smoke, native-check and golden-update
targets are test interfaces documented in [END_TO_END_TESTING.md](END_TO_END_TESTING.md),
[REGRESSION.md](REGRESSION.md) and beside their Makefile recipes. Running a build
or check is not authorization to sign with production credentials, install
trust, create cloud infrastructure, publish a release or change a source account.

## Platform signing recipes

These are explicit maintainer operations. Read the linked credential, signing
and publication prerequisites first. Examples do not authorize installing trust,
using production credentials or publishing a tag.

### Build the Python DMG

Preconditions and interpretation: [MACOS_DISTRIBUTION](MACOS_DISTRIBUTION.md).

```sh
make dmg
```

### Check the installed macOS executable

Preconditions and interpretation: [MACOS_DISTRIBUTION](MACOS_DISTRIBUTION.md).

```sh
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --self-test
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --self-test-gui
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --cli --help
```

### Select a Developer ID identity

Preconditions and interpretation: [MACOS_DISTRIBUTION](MACOS_DISTRIBUTION.md).

```sh
make list-signatures
make dmg-signed
```

### Publish a matching annotated tag

Preconditions and interpretation: [MACOS_DISTRIBUTION](MACOS_DISTRIBUTION.md).

```sh
git switch main
git pull --ff-only origin main
release_tag=vVERSION  # Replace VERSION with the canonical version in pyproject.toml.
make release-tag-check GITHUB_REF_NAME="$release_tag"
git tag -a "$release_tag" -m "Release $release_tag"
make release-tag-check GITHUB_REF_NAME="$release_tag" ARGS=--require-annotated
git push origin "refs/tags/$release_tag"
```

### Inspect signing identities

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
security find-identity -v -p codesigning
```

### Copy the encrypted certificate export

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
base64 -i "$HOME/Signing/DeveloperIDApplication.p12" | pbcopy
```

### Clear the credential clipboard

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
pbcopy < /dev/null
```

### Build with an explicit signing identity

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
make dmg ARGS='--signing-identity "Developer ID Application: YOUR NAME (TEAMID)"'
```

### Build an unsigned local artifact

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
make dmg ARGS='--signing-identity -'
```

### Submit a signed DMG for notarization

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
DMG="$PWD/dist/Email-Collection-Toolkit-VERSION-ARCH.dmg"
NOTARY_PROFILE='mail-archiver-release'

# Prompts securely for authentication; do not enter passwords in shell history.
xcrun notarytool store-credentials "$NOTARY_PROFILE"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" \
  --wait --timeout 30m --output-format json > "$DMG.notarization.json"
```

### Hash the validated DMG

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
(cd "$(dirname "$DMG")" && shasum -a 256 "$(basename "$DMG")")
```
