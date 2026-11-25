<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Developing Email Collection Toolkit

This guide is for working on the source code. To use the application, see the
[user manual](https://simsong.github.io/email-collection-toolkit/manual/). To
build signed installers and publish a release, see [Releasing](RELEASING.md).
Behavior rules for contributors, human or AI, are in [AGENTS.md](../AGENTS.md).

Never use a personal archive for development or tests without explicit
authorization. Use the synthetic fixtures or a copied subset.

## Get started

You need Git, [uv](https://docs.astral.sh/uv/), GNU Make, ClamAV's `libclamav`
(for example `brew install clamav` on macOS), and Rust through
[rustup](https://rustup.rs/), which installs the pinned compiler with rustfmt and
Clippy from `rust-toolchain.toml`. uv provides Python 3.12 or newer. The
browser tests need Chromium, which `make install-test-browser` installs.

```console
git clone https://github.com/simsong/email-collection-toolkit.git
cd email-collection-toolkit
uv sync --locked
make freshclam        # seed local virus definitions in etc/clamdb
make gui              # run the desktop application from the checkout
make install-test-browser
make check            # lint, type checks, and all tests
```

Run any CLI command from the checkout with `uv run --locked ect COMMAND`. Set
`MAIL_ARCHIVE_DIR=/path/to/archive` to avoid repeating `--archive`.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/mailarchiver/` | Python application: import, processing, search, desktop host |
| `src/mailarchiver/plugins/` | Source, file, and processor plugins ([plugin contract](PLUGINS.md)) |
| `gui/` | HTML and JavaScript desktop interface |
| `rust/` | PST importer, MIME tools, verifier, and other Rust programs |
| `converters/pff/` | Separate OST/PST converter with its own environment |
| `tests/`, `e2e_tests/` | Unit and end-to-end tests |
| `scripts/` | Packaging, release, website, and benchmark scripts |
| `benchmarks/import/` | Import benchmark results |
| `website/` | Project website and user guide (Zola) |
| `doc/requirements/`, `doc/implementation/` | Requirements and design by subsystem |
| `research/`, `validation/` | Name-matching research and public-corpus validation |

## Everyday commands

| Command | Does |
| --- | --- |
| `make gui ARGS='...'` | Run the desktop from the checkout |
| `make check` | Ruff and Pylint, then ty and Pyright, then Rust, license and website checks, then both test suites |
| `make check-static`, `make check-tests` | The static half and the test half; CI runs them as parallel jobs |
| `make ruff`, `make lint`, `make types` | Individual static checks. Ruff must pass with zero diagnostics |
| `make test`, `make test-e2e` | Unit suite and end-to-end suite |
| `make benchmark-import` | Time a synthetic import; see [below](#performance-benchmark) |
| `make website-preview` | Serve the website at `127.0.0.1:1111` |
| `make website-check`, `make website-download-check` | Website links and rendered download checks |
| `make security-lock` | Refresh dependencies that have security alerts, across every environment |

The [full list of Make targets](#make-targets) is at the end of this guide.

## Tests

The ordinary suite uses static MBOX and `.emlx` fixtures. The end-to-end suite
copies a tracked, virus-free corpus, ingests 210 observations, and checks
deduplication, autosave exclusion, quarantine, newline preservation,
attachment indexing, BagIt fixity, and the standalone verifier. Headless
Chromium drives the real HTML and JavaScript through the Python service bridge.
Antivirus tests build the EICAR signature from fragments in a temporary
directory and scan it with the real engine; no virus signature is in Git.

- Install the pinned headless Chromium once with `make install-test-browser`,
  or use an installed Chrome with `make check PYTEST_ARGS='--browser-channel chrome'`.
  Windows CI uses Edge.
- If the desktop is importing while you run tests, give the tests their own
  temporary root so lock tests do not collide with the live import:

  ```sh
  mkdir -p .tmp/test-tmp
  TMPDIR="$PWD/.tmp/test-tmp" make check
  ```

- Regenerate the committed end-to-end corpus after an intentional fixture
  change with `make fixture-e2e`.
- Every substantive test names the requirement it checks. Do not add
  coverage-only tests or unnecessary mocks.

Native macOS checks open real windows, need an unlocked desktop, and are
opt-in. They do not run in CI.

| Target | Opt-in variable | Checks |
| --- | --- | --- |
| `test-native-gui` | | Hidden Cocoa/WKWebView bridge |
| `test-native-html-find` | | WKWebView find highlighting and scrolling |
| `test-native-quit` | `MAILARCHIVER_NATIVE_GUI_E2E=1` | Quit during status delivery |
| `test-native-reprocessing` | `MAILARCHIVER_NATIVE_APPLICATION_E2E=1` | Reprocess menu confirmation and Cancel |
| `test-native-worker-preferences` | `MAILARCHIVER_NATIVE_APPLICATION_E2E=1` | Preferences window, Close, saved edits |
| `test-quit` | | Process exit, message-boundary stops, recovery |

Other focused targets include `test-ingest-progress`, `test-ingest-recovery`,
and `test-definition-refresh`. The test architecture and its coverage limits
are in [End-to-end testing](END_TO_END_TESTING.md). Public-corpus validation
runs locally or on explicitly provisioned EC2 workers; see the
[regression policy](REGRESSION.md) and [validation setup](../validation/README.md).

### Windows CI timing diagnostics

Windows CI runs build, reader/scanner, writer, Outlook, GUI/native, and
relocated-package checks as separate steps. To collect timing profiles:

```console
make test-ci-diagnostics
make test-python-writer CI_DIAGNOSTICS=1
make test-pff CI_DIAGNOSTICS=1
```

Each selected test writes `report.json` (phase wall and CPU time, I/O counts,
per-thread operation timings), `calls.txt` (ranked operations), and
`threads.txt` (stacks at phase changes) under `CI_DIAGNOSTICS_DIRECTORY`
(default `dist/ci-diagnostics`). The `msix-build-logs-x64` artifact keeps them
after a failure. Look for time in `os.fsync`, `storage_sync.replace_file`,
SQLite, and lock or subprocess waits. Nested timings overlap, so do not add
them up, and profiling alone cannot prove a disk or antivirus cause. Do not
combine profiling with pytest's `faulthandler_timeout`; the plugin rejects it.
Profiles never record mail bodies, credentials, SQL text, or frame locals.

## Performance benchmark

`make benchmark-import` builds a deterministic 50 MB synthetic corpus (4,804
messages, fixed seed), imports it into a scratch archive with `--no-scan`, and
appends a row with per-phase timings to `benchmarks/import/results.jsonl`.
Use `BENCH_MB=5` for a quick run. Compare rows only from the same machine. See
[the benchmark README](../benchmarks/import/README.md) for the corpus mix
and recorded results.

`make benchmark-search BENCH_ARCHIVE=<archive>` times desktop text search end to
end on an existing archive, opened read-only. Each query (`BENCH_QUERIES`,
default `gandalf`) runs in a fresh process: first page, count, then both warm.
`BENCH_VOCABULARY=empty` times an index never searched before. Rows go to
`benchmarks/search/results.jsonl`; the target fails when a first page takes 1 s
or a first page plus count takes 5 s. See
[the search benchmark README](../benchmarks/search/README.md).

## Outlook PST and OST

Build the helpers with `make pst-importer mcti-scan pff-converter`. Rust and
Cargo are needed; the pinned compiler comes from `rust-toolchain.toml`. The
macOS DMG and Windows MSIX both bundle `pst-importer`, `mcti-scan`, and
`pff-converter`, so users need no compiler. [PST/OST importers](PST_IMPORTER.md)
covers formats, limits, and stream composition, and
[MCT Importer API](MCT_IMPORTER_API.md) covers the importer contract.

PST test inventories live in [`pst/`](../pst/README.md). `make pst-download-plan`
shows the plan and `make pst-download` fetches into ignored `var/pst/`.

## Rust tools

`make rust-programs` builds the supported binaries into `target/release/`
(`RUST_TARGET_DIR` overrides it; direct Cargo uses `CARGO_TARGET_DIR`). Build
one with `make TOOL_NAME`.

| Binary | Interface |
| --- | --- |
| `archive-verifier` | `ARCHIVE`: read-only catalog, MBOX, and search consistency. Stop writers first |
| `mcti-generator` | `COUNT`: synthetic mboxrd to stdout |
| `mdti-validator` | `[--max-message-bytes N]`: validate stdin; nonzero on failure |
| `mcti-scan` | `INPUT.mboxrd`: scan reconstructed messages and emit the API stream |
| `pst-importer` | `[--only-invalid] [--offset N] [--limit N] [--] FILE`: read-only PST extraction |
| `pst-downloader` | Corpus download; see [pst/README.md](../pst/README.md) |

`make verify-database ARCHIVE=...` runs the verifier, and
`make pst-import PST=FILE ARGS='...'` runs the importer. A nonzero importer
exit can mean partial results, which must be kept and reported. The Python
`pff-converter FILE` accepts `--receipt`, `--diagnostics`, `--timeout-seconds`
(default 60), `--max-message-bytes` (default 64 MiB), `--max-folder-depth`
(default 64), `--offset`, and `--limit`. The retired Rust GUI is described in
[RUST_GUI_EXPERIMENTS.md](RUST_GUI_EXPERIMENTS.md).

## Antivirus runtime

Scanning uses the embedded `libclamav` engine in a worker process. There is no
daemon. `make freshclam` seeds and updates `etc/clamdb` for development.
Definitions older than 24 hours are refreshed before a scan when the update
site is reachable; offline or failed updates keep the existing definitions.
A pinned `MAILARCHIVER_CLAMAV_DATABASE` turns refresh off.

macOS CI and release jobs build a checksum-pinned ClamAV and OpenSSL with
`make ci-clamav`, and `make ci-clamav-check` runs real scanner and packaging
checks against it. On Windows, `make prepare-windows-clamav` stages the pinned
portable x64 runtime in `.tmp/clamav-x64`. `make clamav-update` refreshes
per-user definitions, and `make clamav-seed SEED_DIRECTORY=...` copies and
validates an existing set. See [Embedded antivirus](EMBEDDED_CLAMAV.md).

## Attachment text extraction

Plain-text, HTML, and RTF attachments are indexed today. The agreed plan (issues #159,
#163 and #19) extracts words from PDF and Office files with per-format Python
libraries and command-line tools, uses OCRmyPDF and Tesseract for scans and
images, and runs each extractor in a sandboxed worker with timeouts. It does
not use Apache Tika or Java.

## Desktop internals

The desktop is Python and pywebview on both platforms: WKWebView on macOS and
WebView2 with a private x64 CPython on Windows. On macOS, `make gui` creates an
ad-hoc-signed development application under `.tmp/` that reuses the virtual
environment and keeps its Keychain access. Packaged applications have their own
credential identity. See [delivery implementation](implementation/DELIVERY.md).

### Windows

Use native Windows, not WSL, for anything involving file locking, dialogs,
Explorer, or packaging. You need Git, uv, x64 Python 3.12+, and GNU Make with a
working shell. Rust, MSVC, and a Windows SDK are needed only for the Rust tools.
`make test-python-reader` and `make test-python-reader-native` check reading,
export, and the native bridge. Ctrl shortcuts replace Command shortcuts.
Windows ARM machines run the x64 package under emulation; there is no native
ARM64 package. See [Releasing](RELEASING.md#windows) for packaging and the
installer checklist.

## Research and diagnostic commands

These write private derivatives to outputs you name. They never change sources
or canonical messages.

| Target | Does |
| --- | --- |
| `make data-quality-audit ARCHIVE=PATH EARLY_SOURCE=PATH AUDIT_OUTPUT=NEW_DIR` | Export early-message and missing-sender evidence |
| `make data-quality-babyl-audit EARLY_SOURCE=PATH AUDIT_OUTPUT=DIR` | Inspect Babyl source records |
| `make data-quality-summary AUDIT_OUTPUT=DIR` | Summarize exported evidence |
| `make name-matcher-observations ARCHIVE=PATH OUTPUT=NEW.sqlite3` | Export identity and signature evidence |
| `make addressbook-export ARGS='--archive PATH --output NEW.txt'` | Experimental ePADD address book |

`ect h3-review --help` describes the semantic-hash review exporter. Name
matching research uses its own locked environment in
[`research/name-matcher`](../research/name-matcher); see the
[research notebook](research/name-matcher/README.md).

## Environment variables

User-facing runtime variables (`MAIL_ARCHIVE_DIR`, the ClamAV overrides and TLS
trust) are listed on the website's
[Command line](https://simsong.github.io/email-collection-toolkit/command-line/#environment-variables)
page. Build, test, signing and research settings are below. Never document
secret values. Keep new settings documented here and beside their Makefile
targets. Makefile variables are target parameters, not automatically process
environment variables. Legacy `MAILARCHIVER_CLAMD*` variables are unused.

| Variable / Make parameter | Purpose |
| --- | --- |
| `CARGO`, `RUST_TARGET_DIR`, `CARGO_TARGET_DIR` | Cargo executable and build output; Make uses `RUST_TARGET_DIR`, direct Cargo uses `CARGO_TARGET_DIR` |
| `RUSTUP_TOOLCHAIN` | Override the compiler selected by `rust-toolchain.toml` |
| `CARGO_BUILD_JOBS` | Limit native Rust compilation parallelism |
| `OPENSSL_DIR`, `OPENSSL_LIB_DIR`, `OPENSSL_INCLUDE_DIR` | Select the OpenSSL installation for Rust dependencies |
| `OPENSSL_NO_VENDOR`, `PKG_CONFIG_PATH` | Control vendored OpenSSL and package metadata lookup |
| `TMPDIR` | Optional disposable-test temporary root; all test subprocesses share it so lock tests stay separate from a running desktop import |
| `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `UV_PYTHON_BIN_DIR` | Locations for uv's cache, managed Python, and interpreter links |
| `UV_PROJECT_ENVIRONMENT`, `PYTHONPATH` | Isolated packaging environment and repository-local test import path |
| `FQDN` | Disposable IMAP tests use `localhost` to avoid reverse DNS |
| `CI_DIAGNOSTICS`, `CI_DIAGNOSTICS_DIRECTORY` | Opt-in synthetic writer/OST test profiles (`1` enables); output directory, default `dist/ci-diagnostics` |
| `MAILARCHIVER_PST_ACTIVITY_FILE` | Host-owned, newly created eight-byte PST work-counter snapshot; separates extraction liveness from batched diagnostic history |
| `MAILARCHIVER_NATIVE_APPLICATION_E2E`, `MAILARCHIVER_NATIVE_GUI_E2E` | Opt into visible native tests; focus/menu acceptance requires an unlocked desktop |
| `GITHUB_ENV`, `RUNNER_TEMP` | GitHub Actions environment handoff file and temporary area |
| `GITHUB_REPOSITORY`, `GH_TOKEN`, `GITHUB_REF_NAME` | Release repository, API authorization, and tag being validated |
| `SIGNING_IDENTITY` | Local macOS Developer ID signing identity |
| `SPARKLE_ED25519_PRIVATE_KEY_BASE64` | Protected key used to sign installers and update feeds |
| `APPLE_CERTIFICATE_P12_BASE64`, `APPLE_CERTIFICATE_PASSWORD` | Protected Developer ID certificate export and its password |
| `APPLE_NOTARY_KEY_ID`, `APPLE_NOTARY_ISSUER_ID`, `APPLE_NOTARY_PRIVATE_KEY_BASE64` | Protected Apple notarization credentials |
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | Windows signing OIDC identity and subscription |
| `AZURE_SIGNING_ENDPOINT`, `AZURE_SIGNING_ACCOUNT`, `AZURE_SIGNING_PROFILE` | Microsoft public signing service selection |
| `MSIX_PUBLISHER`, `PUBLIC_SIGNING` | Approved Windows publisher and workflow signing-mode selector |
| `MSIX_TEST_CERT_PFX_BASE64` | Protected persistent test-signing key; never used for public releases |
| `DEVELOPER_DIR`, `TOOLCHAINS`, `SDKROOT`, `CODESIGN_ALLOCATE` | Apple toolchain overrides; discarded during historical-feed trust checks |
| `__PYVENV_LAUNCHER__`, `PYTHONEXECUTABLE` | Interpreter selection handled internally by the macOS development launcher |
| `DEFINITIONS_DIRECTORY`, `SEED_DIRECTORY` | New definition-download destination and existing baseline to copy |
| `ARGS`, `ARCHIVE`, `IMAP_ARGS` | Makefile command arguments and archive selection; never password channels |
| `RELEASE_TAG`, `APPCAST`, `WINDOWS_ARCHIVE`, `WINDOWS_RELEASE_URL` | Candidate release tag, feed path, and Windows signing inputs |
| `MSIX_PACKAGE`, `MSIX_EVIDENCE`, `MSIX_PAYLOADS` | Windows package, relocation-test evidence, and bundle inputs |
| `LICENSE_OUTPUT` | Destination for a generated runtime license bundle |
| `NAME_RESEARCH_ENV`, `EVIDENCE`, `OUTPUT`, `DATABASE`, `LABELS` | Research environment, observations, private outputs, frozen experiment, and owner-review labels |

## Make targets

| Target | Purpose / parameters |
| --- | --- |
| `sync-dependencies` | Synchronize the Python environment |
| `build-sdist` | Source distribution; `DIST_DIR` output |
| `gui` | Launch the desktop; `ARGS` passes its options |
| `dmg`, `python-dmg`, `dmg-signed`, `list-signatures`, `notarize-dmg` | macOS packaging; see [Releasing](RELEASING.md) |
| `check-release`, `test-dmg`, `test-mounted-dmg`, `preview-dmg` | macOS candidate acceptance |
| `prepare-windows-clamav`, `prepare-windows-updater`, `msix-test`, `test-msix`, `msix-bundle`, `verify-public-msix` | Windows packaging; see [Releasing](RELEASING.md#windows) |
| `sparkle-tools`, `sparkle-keys`, `update-appcast`, `check-appcast` | Update tooling and feeds |
| `release-files`, `release-tag-check`, `release-candidate-base`, `release-candidate-feed`, `release-appcast-base`, `release-package-check` | Release gates |
| `sign-historical-appcast` | One-time reviewed historical feed migration |
| `freshclam`, `clamav-update`, `clamav-seed` | Virus definitions |
| `website-check`, `website-build-check`, `website-download-check`, `website-preview` | Website checks and preview |
| `website-release-data`, `website-screenshots`, `website-preview-screenshots`, `website-icons`, `website-gmail-illustrations` | Website data and images |
| `install-test-browser` | Install the pinned test browser |
| `ocr-inventory`, `ocr-profile`, `ocr-analyze`, `ocr-run`, `ocr-experiment` | Experimental OCR fixture pipeline; see [PDF/OCR plan](plans/PDF_OCR.md) |
| `check`, `check-static`, `check-tests` | Ordered full, static, and test verification |
| `ruff`, `pylint`, `ty`, `pyright`, `lint`, `types`, `syntax-check` | Individual static checks |
| `rust-check`, `rust-fmt`, `rust-lock`, `rust-toolchain` | Rust workspace checks, formatting, and dependencies |
| `runtime-license-check`, `runtime-license-bundle`, `copyright-check`, `distribution-check` | License and distribution audits |
| `benchmark-import`, `benchmark-name-resolution`, `benchmark-search` | Benchmarks |

The remaining `test-*`, `fixture-*`, smoke, and golden-update targets are
described in [End-to-end testing](END_TO_END_TESTING.md),
[REGRESSION.md](REGRESSION.md), and beside their Makefile recipes. Running a
build or check never authorizes production signing, installing trust, creating
cloud infrastructure, publishing a release, or changing a source account.
