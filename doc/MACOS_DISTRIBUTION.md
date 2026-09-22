<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# macOS application and DMG

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

## Build and test

These instructions build the current Python/pywebview application. The compiled
replacement candidates are [Dioxus Desktop and Tauri](DIOXUS.md), with the Python archive
engine bundled alongside it. That migration has not changed `make dmg` or its
validation; no Dioxus DMG is produced by the current build.

Plan comparable trial implementations in Dioxus and Tauri before choosing a
framework. Either approach retains the Python archive engine.

On a logged-in Mac with this checkout's development environment and `uv`:

```sh
make dmg
```

The target installs only project-local packaging dependencies, then uses
PyInstaller to bundle Python, Python extension libraries, Cocoa/WKWebView
bindings, SQL/YAML resources, plug-in manifests, GUI assets, and the standalone
verifier source. It uses the system WebKit, not a downloaded browser. Users
need no Python, `uv`, Homebrew, or source checkout for the supported local-mail
GUI. ClamAV is optional. Experimental PDF extraction/OCR, Tika/Java and the
Apple Intelligence command are not included in this desktop workflow.

The [planned ingest executables](PST_DUAL_READER.md#executables-and-installers)
add selected native helpers, starting with a Rust PST adapter candidate. These
are not included today. Delivery requires nested signing, dependency audits,
notarization/stapling and installed-fixture tests on each supported architecture.
A JVM would be needed only if a Java importer is later bundled; Tika remains
outside the supported desktop runtime.

Schema management requires no Java runtime or external migration tool. SQL files
use Flyway naming conventions only (`V<version>__<description>.sql`); Flyway is
not a dependency and is not bundled. Application-managed SQLite catalog upgrades
remain planned; the current app validates the supported schema and rejects
incompatible catalogs rather than upgrading them.

The initial build uses the native Python architecture (currently arm64), not
universal2. Intel builds require a matching Intel Python and dependencies and
their own validation. Compatibility with older macOS releases must be tested
on those releases; success on the build Mac is not a compatibility matrix.

Output: `dist/Email-Collection-Toolkit-VERSION-ARCH.dmg` with Developer ID
signing, or `dist/Email-Collection-Toolkit-VERSION-ARCH_UNSIGNED.dmg` otherwise. The volume contains
`Email Collection Toolkit.app` on the left and a shortcut to `/Applications` on the right.
A pale-blue background shows the app title, a right-pointing arrow, and
**Drag Email Collection Toolkit to Applications to install**. The 720-by-480-point Finder
window uses large icons and no toolbar/sidebar; there is no separate instruction
file to open. Its 720-by-420-point background leaves room for Finder's window
chrome so the footer stays visible. A build-only `dmgbuild` dependency saves this layout, and the
mounted checks verify it. Use `make preview-dmg DMG=/absolute/path/to/image.dmg`
for visual review in Finder; press Return in the terminal to eject afterward.
The `.mailarchive` package type is declared in
Info.plist; the Cocoa delegate handles document-open events and retains
pywebview's close/ingest safeguards. File Open also accepts extensionless archive
directories. Installation does not force replacement of another default handler.

Ordinary `make dmg` and `make dmg-signed` mount the candidate DMG read-only and
run its headless self-test, with a system-only PATH and no Python environment
overrides. They do not open GUI test windows.

`make check-release` builds and validates a DMG with both headless and GUI tests.
Use `make check-release DMG=/absolute/path/to/image.dmg` to validate an existing
build instead. This is an explicitly requested local, interactive validation.
The GitHub release workflow uses headless `make dmg` on its hosted macOS runner;
its mounted installed-app self-test runs without opening native windows.
It announces the GUI self-test before opening synthetic About, search, Ingests,
and source-picker windows. These windows close automatically. A GUI worker
that remains after the five-second shutdown grace period fails the build with
a thread dump; a printed test report alone is not evidence of process exit.
It verifies the code-signature seal and always attempts to detach the volume.
It also audits every bundled Mach-O file for external non-system library paths.
Ejection retries briefly if macOS still holds the volume; cleanup is nonrecursive
and never traverses a still-mounted filesystem.
Only a passing candidate replaces the prior DMG. JSON test reports sit beside
the final image. Tests create disposable archives and isolated document
preferences; they do not open or import into the user's last archive.

```sh
make self-test
make self-test-gui
make test-packaging
make test-dmg DMG="/absolute/path/to/Email-Collection-Toolkit-0.0.0-arm64.dmg"
make check-release DMG="/absolute/path/to/Email-Collection-Toolkit-0.0.0-arm64.dmg"
```

The first target displays no windows. The second shows and closes the real
About, search, Ingests, and source-picker windows automatically. Both test actual
unscanned ingest, exact-byte retrieval, FTS search, independent preservation
verification, and repeat-import idempotence. The GUI additionally checks native
bridge calls, the missing-scanner banner and canceled source selection.
Each has a watchdog and returns nonzero on failure.

The shipped executable also accepts `--self-test`, `--self-test-gui`, and
`--report /absolute/path/report.json`. Advanced users can access the bundled
archive CLI with `--cli`, without a separately installed Python:

```sh
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --self-test
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --self-test-gui
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --cli --help
```

## Optional antivirus

The [embedded antivirus migration planned for PR #124](EMBEDDED_CLAMAV.md)
will replace this setup with a bundled library and offline definitions, with
updates under Application Support and refreshed app releases at least quarterly.
The DMG bundles libclamav, FreshClam, and the definitions in `etc/clamdb/`.
Install ClamAV for development, then run `make freshclam`: it seeds the ignored
project directory from an installed database (normally
`/opt/homebrew/var/lib/clamav/` on Apple Silicon Homebrew) and updates that copy.
`make dmg` uses those local definitions; release CI runs `make freshclam` first.
The installed app needs no Homebrew installation or resident daemon. Its mounted
self-test checks real clean/EICAR scans and native dependency paths.

User-requested definition updates go to
`~/Library/Application Support/Email Collection Toolkit/clamav/`.
Windows uses `%LOCALAPPDATA%\Email Collection Toolkit\clamav\`.
The project uses GPL-2.0-only, matching ClamAV's license version. Other dependency
license compatibility and source/notice collection remain unresolved; see
[embedded antivirus](EMBEDDED_CLAMAV.md).

Missing executables/configuration produce an **Antivirus unavailable** banner.
The import confirmation defaults to Cancel; **Import Without Scanning** is an
explicit opt-out for that import only. It is recorded in run status and in
each new message's antivirus metadata defect. Import history displays the
warning after restart. A configured scanner's startup/scan failure stops import
and never silently opts out. Previously archived messages are not retroactively
scanned by a later ordinary import; a rescan command remains future work.

## Signing and Apple account renewal

For local signing with a certificate and private key already in your Keychain:

```sh
make list-signatures
make dmg-signed
```

`dmg-signed` defaults to the first valid **Developer ID Application** identity
listed by macOS. It fails before building if none is available. To select a
different identity, use `make dmg-signed SIGNING_IDENTITY=HASH`, using the
certificate hash from `list-signatures`. The target signs both the application
and DMG; notarization remains separate.

On an isolated GitHub-hosted runner, `make dmg` and `make check-release` import a Developer ID Application
identity when both
`APPLE_CERTIFICATE_P12_BASE64` and `APPLE_CERTIFICATE_PASSWORD` are available.
The [certificate management guide](CERTIFICATE_MANAGEMENT.md) explains exporting
the `.p12`, App Store Connect API key, and six GitHub Actions repository
secrets. A tagged release additionally requires `APPLE_NOTARY_KEY_ID`,
`APPLE_NOTARY_ISSUER_ID`, and `APPLE_NOTARY_PRIVATE_KEY_BASE64`, which is the
Base64 encoding of the complete `.p8` API private-key file. The workflow builds,
notarizes, staples, Gatekeeper-validates, and retests the resulting DMG before
it becomes a release artifact. The release tag must contain this workflow and
builder; dispatching an older tag does not retrofit the new builder. Release
tags must be annotated and match the project version; Git-tag signatures and
release-signing public keys are not required. Automatic imports are rejected
locally and on self-hosted runners because `security` password arguments remain
visible to other processes; use an existing keychain identity for local signing.
The separate `SPARKLE_ED25519_PRIVATE_KEY_BASE64` secret contains Sparkle's
already-Base64 exported update key. The release stays draft while its final
DMG is signed for the appcast. The workflow commits the feed to `main`, then
publishes the draft and explicitly dispatches GitHub Pages from `main`.

Missing either secret is nonfatal: the build emits an Actions warning, leaves
the DMG container unsigned, and names it `*_UNSIGNED.dmg`. An explicitly supplied
identity takes precedence; `--signing-identity -` forces the unsigned path.
Both signing secrets present but invalid is a build failure, not an unsigned
fallback. A release with missing or invalid notarization credentials also fails;
an unsigned development image is never uploaded as a release. The imported
signing private key and temporary notarization `.p8` file are deleted when their
steps finish, and the keychain search list is restored.

Without a Developer ID, PyInstaller and `codesign` use **ad-hoc signing** (`-`).
This makes the bundle internally verifiable; it does not establish trusted
publisher identity or satisfy Gatekeeper/notarization. A self-signed certificate
would not solve that trust problem either. Downloaded builds may be blocked.
Only for a copy the user trusts, macOS provides **System Settings → Privacy &
Security → Open Anyway** after an attempted launch. Never disable Gatekeeper.
The mounted local tests do not establish downloaded-file Gatekeeper acceptance.

1. Sign in with the Apple Account used for the previous developer membership.
   Follow Apple's [renewal instructions](https://developer.apple.com/help/account/membership/renewal/).
   Web enrollment renews through the account; app enrollment uses its subscription.
   If no renewal action is available, contact Apple Developer Support.
2. Once membership is active, obtain a **Developer ID Application** certificate
   and matching private key through Xcode or Certificates, Identifiers & Profiles.
   Renewing membership does not renew an expired signing certificate. Follow
   Apple's [Developer ID instructions](https://developer.apple.com/help/account/certificates/create-developer-id-certificates).
   Developer ID Installer is for `.pkg` installers, not this drag-install DMG.
3. Build with the default Developer ID Application identity from your Keychain:

   ```sh
   make dmg-signed
   ```

4. For a tagged release, GitHub Actions submits the DMG with `notarytool`,
   staples the accepted ticket, validates it, and requires Gatekeeper
   acceptance. The API `.p8` file is Base64-encoded into the protected
   `APPLE_NOTARY_PRIVATE_KEY_BASE64` repository secret; neither it nor the
   certificate password is stored in the repository. Follow Apple's
   [notarization workflow](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).

## Publish a tagged macOS release

After the intended version change is merged into `main`, use a clean checkout
and replace the example tag with `v` plus the exact `pyproject.toml` version.
Do not move or reuse an already published tag.

```sh
git switch main
git pull --ff-only origin main
release_tag=v1.0.0a3
make release-tag-check GITHUB_REF_NAME="$release_tag"
git tag -a "$release_tag" -m "Release $release_tag"
make release-tag-check GITHUB_REF_NAME="$release_tag" ARGS=--require-annotated
git push origin "refs/tags/$release_tag"
```

The tag push starts [the release workflow](../.github/workflows/release.yml)
for tags beginning with `v`. It checks that the tag is annotated and matches
the version in that tagged commit; `scripts/release_tag.py` also selects the
Sparkle preview channel for `aN`/`bN` versions and the release channel for a
stable version. The macOS build job installs dependencies and ClamAV,
then runs `make dmg`, `make notarize-dmg`, and `make test-dmg`.
These targets call `scripts/build_macos.py` and its `scripts/macos_signing.py`
helpers to build, Developer ID-sign, notarize, staple, and test the mounted
image. The assemble job validates distributions, builds source archives,
downloads the tested DMG, and creates a draft GitHub release with SHA-256
checksums. It uses `make sparkle-tools` and `make update-appcast` to run
`scripts/update_appcast.py` with Sparkle's `sign_update` on the final DMG.
Only after signing the feed item does it commit
`website/static/updates/mac/appcast.xml` to `main`, publish the draft release,
and explicitly dispatch [the Pages workflow](../.github/workflows/pages.yml)
from `main`. Pages runs `scripts/update_site_releases.py`, builds the Zola site,
and deploys it to GitHub Pages. The appcast commit message includes `[skip ci]`
in [release.yml](../.github/workflows/release.yml), which skips push-triggered
Actions. More importantly, the workflow uses `GITHUB_TOKEN` to commit the feed
and publish the release, so neither event starts another workflow. Its explicit
`workflow_dispatch` is what starts [Pages](../.github/workflows/pages.yml);
the tag push alone does not deploy the site.
If a release step fails before publication, inspect the Actions run and draft
release rather than assuming the DMG or website is live.

The release workflow accesses these GitHub Actions repository secrets, scoped
to the steps that need them:

| Step | Secrets | Purpose |
| --- | --- | --- |
| Build, sign, and notarize DMG | `APPLE_CERTIFICATE_P12_BASE64`, `APPLE_CERTIFICATE_PASSWORD` | Import the Developer ID Application identity on the hosted runner. |
| Build, sign, and notarize DMG | `APPLE_NOTARY_KEY_ID`, `APPLE_NOTARY_ISSUER_ID`, `APPLE_NOTARY_PRIVATE_KEY_BASE64` | Authenticate `notarytool` with the App Store Connect API key. |
| Sign appcast item | `SPARKLE_ED25519_PRIVATE_KEY_BASE64` | Sign the final DMG for Sparkle update verification. |

The assemble job also uses the automatically provided `GITHUB_TOKEN` to create
and publish the release, update the appcast on `main`, and dispatch Pages; it
is not an additional repository secret to configure. The Pages workflow uses
its own token and Pages deployment permissions, not the six release secrets.
See [certificate management](CERTIFICATE_MANAGEMENT.md) for secret setup.

The website's release-link helper currently recognizes stable `v1.0.0` tags
and legacy `v1.0.0-beta1` tags, but not the current PEP 440 preview spelling
(`v1.0.0a2`/`v1.0.0b1`). The Sparkle appcast uses the current spelling; until
the helper is updated, a successful Pages deployment does not guarantee that
the site's preview download link points to the new preview release.
