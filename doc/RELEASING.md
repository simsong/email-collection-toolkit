<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Releasing

This guide covers versions, signed installers, update feeds, and publication.
Start with [Developing](DEVELOPING.md) for a working checkout. Every step here
that signs with production credentials, installs trust, pushes a tag, or
publishes a release needs explicit authorization from the maintainer.

Detailed references:

- [macOS distribution](MACOS_DISTRIBUTION.md): DMG contents, signing, notarization, and acceptance.
- [Certificate management](CERTIFICATE_MANAGEMENT.md): Developer ID certificates and their renewal.
- [Application updates](SPARKLE_UPDATES.md): Sparkle and WinSparkle feeds and their lifecycle.
- [DevOps](DEVOPS.md): CI, release, and website workflow triggers and gates.
- [Licensing](LICENSING.md): what each shipped component requires.

## Versions

`pyproject.toml` holds the canonical PEP 440 version. The shared
`release_versions.py` mapper derives tags, update channels, and monotonically
increasing Sparkle build numbers from it. Versions are `MAJOR.MINOR.PATCH`,
with numbered `aN` or `bN` previews. Never hard-code a candidate version, tag,
or build number.

Each user-visible fix handed back for testing advances the version and
`uv.lock`; alpha and beta fixes advance the prerelease number. About shows the
launch or build commit and discloses local changes.

## Release sequence

1. Merge the release changes to `main` through review.
2. Opt a branch into signed installer checks with `[release-ci]` in the commit
   message. `make release-candidate-base APPCAST=...` authenticates the
   published feed, and `make release-candidate-feed APPCAST=...` signs and
   verifies a candidate feed without creating a release or tag.
3. Run the [Windows installer checklist](#windows-installer-checklist) on the
   candidate.
4. Push an annotated tag that matches `pyproject.toml` (see
   [Publish a matching annotated tag](#publish-a-matching-annotated-tag)). A
   pushed tag is immutable: fix a bad release with a new version and tag.
5. The release workflow verifies the prior feed, builds both installers,
   authenticates their signatures and the complete appcast, and only then
   publishes the release.
6. Pages deploys the website from `main`, resolving release links and verifying
   the signed feed. `make website-download-check` checks the download page.

Release builds must run `make runtime-license-check` on every platform and
include the `make runtime-license-bundle LICENSE_OUTPUT=PATH` output.

## macOS

`make dmg` packages the Python runtime, GUI, plugins, importer tools, ClamAV
engine and definitions, and Sparkle, then mounts the DMG and runs headless
acceptance. `make test-dmg DMG=...` repeats that acceptance, and
`make check-release` adds visible native checks. Building does not install or
publish anything. Use `SIGNING_IDENTITY` for a local Developer ID identity.
Preconditions for every recipe below are in [macOS distribution](MACOS_DISTRIBUTION.md)
and [Certificate management](CERTIFICATE_MANAGEMENT.md).

### Build the Python DMG

```sh
make dmg
```

### Check the installed macOS executable

```sh
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --self-test
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --self-test-gui
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --cli --help
```

### Select a Developer ID identity

```sh
make list-signatures
make dmg-signed
```

### Publish a matching annotated tag

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

```sh
security find-identity -v -p codesigning
```

### Copy the encrypted certificate export

```sh
base64 -i "$HOME/Signing/DeveloperIDApplication.p12" | pbcopy
```

### Clear the credential clipboard

```sh
pbcopy < /dev/null
```

### Build with an explicit signing identity

```sh
make dmg ARGS='--signing-identity "Developer ID Application: YOUR NAME (TEAMID)"'
```

### Build an unsigned local artifact

```sh
make dmg ARGS='--signing-identity -'
```

### Submit a signed DMG for notarization

```sh
DMG="$PWD/dist/Email-Collection-Toolkit-VERSION-ARCH.dmg"
NOTARY_PROFILE='mail-archiver-release'

# Prompts securely for authentication; do not enter passwords in shell history.
xcrun notarytool store-credentials "$NOTARY_PROFILE"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" \
  --wait --timeout 30m --output-format json > "$DMG.notarization.json"
```

### Hash the validated DMG

```sh
(cd "$(dirname "$DMG")" && shasum -a 256 "$(basename "$DMG")")
```

## Windows

The MSIX bundles a private x64 CPython, the application, ClamAV, WinSparkle, and
the PST/OST helpers. WebView2 is external. Source tests, relocated-package
tests, installed activation, upgrade and uninstall, physical interaction, and
updater-driven replacement are separate gates; one does not establish another.
A complete updater-driven Windows upgrade has not been validated yet.

### Windows packaging targets

These run on a Windows host. Outputs stay local unless a workflow publishes them.

| Target | Parameters / purpose |
| --- | --- |
| `prepare-windows-clamav` | Stage the verified x64 runtime and baseline definitions |
| `prepare-windows-updater` | Stage the pinned WinSparkle SDK and notices |
| `msix-test` | `MSIX_ARGS='-Architecture x64'`: build the Python payload |
| `test-msix` | `MSIX_PACKAGE=PATH MSIX_EVIDENCE=NEW_DIR`: validate the extracted payload |
| `msix-bundle` | `MSIX_PAYLOADS=DIR MSIX_ARGS='OPTIONS'`: assemble and sign; `-PublicSigning` prepares input for Microsoft signing |
| `verify-public-msix` | `MSIX_PUBLISHER='approved subject'`: check public trust, timestamp, and identity |
| `test-windows-scanner`, `test-windows-updater` | Native scanner and update filtering |

### MSIX packaging

The current payload is x64 private CPython activated through
`pythonw.exe -I -m mailarchiver.desktop_entry`. It contains the shared GUI,
Python modules, plugin/SQL/YAML resources, dependency licenses, ClamAV runtime
and baseline definitions, WinSparkle, and the PST/OST helpers. WebView2 remains
external; startup
reports a missing/incompatible runtime before creating the webview.

Versions come from `pyproject.toml` through the release mapper. The package
identity is `ECT.PythonReader`, distinct from historical Rust packages. Private
`_pth` configuration excludes checkout and user packages. Outputs and evidence
directories must be new.

#### Build and signing

The [Windows packaging targets](#windows-packaging-targets) give the Make
syntax. The payload builder is `scripts/win/build_windows_msix.ps1`; bundling
and signing are separate steps. The Python-reader reusable workflow supplies
the payload to the MSIX workflow for assembly and installed acceptance.
Ordinary pushes do not build installers; dispatch or explicit candidate/release
triggers opt in. [DEVOPS.md](DEVOPS.md) owns trigger and publication policy.

Release and candidate builds select [Microsoft public signing](#microsoft-public-signing).
Required Azure/GitHub setup must be verified before claiming trusted output.
There is no fallback to development signing. Public ZIPs omit trust helpers.

Development test signing uses the persistent private key from
`MSIX_TEST_CERT_PFX_BASE64`, checked against `scripts/win/test-signing.cer`; it
never generates a substitute key. Only public trust material may accompany the
base test installer. Explicit installation of that certificate in Local Machine /
Trusted People is necessary for a test-signed install; the adjacent helper
requests elevation and does not install the app or change execution policy.
Private PFX material must not enter artifacts.

#### Installed acceptance

Build the base and higher-version upgrade fixture once, then test the same bytes.
Upgrade fixtures are CI-only. Installed checks exercise private-runtime discovery,
registered Start-menu activation, synthetic search/completion/verified reads,
native window startup, Close and File/Quit, upgrade and uninstall. Archive/source
hashes must remain fixed. Test cleanup removes only trust and packages it added.

Extraction/relocation is useful local evidence but does not prove installation,
trust, registered activation or updater-driven replacement. Physical dialogs,
keyboard/drag behavior, scanner/converter availability and current-head signing
need their own evidence. Historical ARM64 Rust tests are recorded only in
[RUST_GUI_EXPERIMENTS.md](RUST_GUI_EXPERIMENTS.md).

### Microsoft public signing

Release and `[release-ci]` candidate calls select `public_signing: true` in
`windows-msix.yml`. Explicit dispatch defaults to local test signing; select public
signing to exercise production. Missing configuration fails the run; there is no
fallback to the test certificate. This implementation still needs Azure identity
approval, a certificate profile, federation, GitHub configuration and hosted CI.

The payload publisher is the exact approved certificate subject in the repository
variable `MSIX_PUBLISHER`. Microsoft retains the private key. The protected
`windows-signing` environment gates the bundle job; only that job requests an OIDC
token. Azure login exchanges the short-lived GitHub token, and Microsoft's pinned
Artifact Signing action signs the base and CI-only upgrade bundles with SHA-256 and
an RFC 3161 timestamp. Windows checks platform trust, timestamps and bundle identity
before upload. Public installation acceptance imports no certificate.

Release staging checks the bundle and Windows verification evidence against the
downloaded hash inventory. This establishes artifact consistency; Windows SignTool
and installation jobs establish trust. Release ZIPs omit test certificates and trust
helpers. Published assets remain immutable. Changing publisher creates a different
Windows package family: remove an old test installation before installing the public
build, while preserving archive directories.

#### Provisioning

1. Complete individual identity validation in Azure Artifact Signing and create a
   **Public Trust** certificate profile. Copy its full subject into `MSIX_PUBLISHER`;
   do not derive a `CN` string from the display name.
2. Create a single-tenant Entra application and service principal without a client
   secret. Add one federated credential: issuer
   `https://token.actions.githubusercontent.com`, audience `api://AzureADTokenExchange`,
   subject `repo:simsong/email-collection-toolkit:environment:windows-signing`.
3. Grant that service principal **Artifact Signing Certificate Profile Signer** on
   the selected profile only. It needs no Owner, Contributor or Identity Verifier
   role. The human verifier needs **Artifact Signing Identity Verifier** on the account;
   inherited Owner alone does not provide validation access.
4. Create `windows-signing` with required reviewer `simsong`, administrator bypass
   disabled, and restricted deployment branches/tags. Permit `main`, version tags
   `v*`, and explicitly approved candidate branches only. Review a candidate's full
   workflow/code before approving signing: the environment subject alone does not
   restrict which workflow can request signing. Allow self-review when necessary
   for the sole human administrator. Protect main/release publication separately.
5. Set the repository variables below, publish the change through review, then run
   public dispatch or candidate CI. Local tests cannot establish Azure access or
   Windows trust. Federation and security-sensitive role assignments require approval.

The created account is `simson-garfinkel-signing`, resource group `rg-simson-signing`,
East US, endpoint `https://eus.codesigning.azure.net/`. Tenant/subscription IDs are
recorded in the private setup memo.

#### Variables and commands

Use repository variables so the payload can read its publisher before entering the
signing environment. Do not shadow the publisher with a different environment value.
Identifiers and names below are configuration, not credentials.

| Variable | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID` | Entra application ID for restricted GitHub federation |
| `AZURE_TENANT_ID` | Tenant containing that application |
| `AZURE_SUBSCRIPTION_ID` | Subscription containing the signing account |
| `AZURE_SIGNING_ENDPOINT` | Region-specific signing endpoint |
| `AZURE_SIGNING_ACCOUNT` | Artifact Signing account name |
| `AZURE_SIGNING_PROFILE` | Approved Public Trust certificate profile name |
| `MSIX_PUBLISHER` | Exact certificate subject for manifest and verification |
| `PUBLIC_SIGNING` | Workflow-selected boolean controlling assembly/installation mode |
| `MSIX_TEST_CERT_PFX_BASE64` | Development test key, used only in test mode |

`make test-release-files` checks staging and its rejection cases. On Windows,
`make msix-bundle MSIX_PAYLOADS=... MSIX_ARGS=-PublicSigning` assembles unsigned
bundles for the Microsoft action. After signing, `make verify-public-msix
MSIX_PUBLISHER='exact approved subject'` checks output. Hosted acceptance exercises
install, native launch, upgrade, archive preservation and uninstall on shared bundles.

#### Maintenance

OIDC removes client-secret renewal. Keep billing active, renew identity validation by
the date shown in Azure, and monitor profile status. Timestamping is required because
signing certificates are short-lived. Review action pins, federation scope, environment
protection and roles when repositories/workflows change. Signing does not guarantee
every Windows warning is suppressed.

References: [Microsoft action](https://github.com/Azure/artifact-signing-action),
[roles](https://learn.microsoft.com/en-us/azure/artifact-signing/tutorial-assign-roles),
[OIDC](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect),
[SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool).

### Windows installer checklist

Use the candidate's base MSIX bundle, not the CI-only upgrade fixture. Record
its filename, SHA-256, Windows version, and the version/Git commit shown in About.
Use a disposable archive and a test IMAP account; source mail must remain unchanged.
Automated branch CI separately checks native WebView2 rendering, Preferences Close,
real ClamAV scanning, relocated packaging, installation, upgrade and uninstall.
A successful build does not establish these manual checks.

- Install and launch without Python or a checkout. Confirm About shows the new
  version and commit, the application icon/name, and the registered processors.
  Open the processor graph and inspect MIME extraction's possible output types.
- Check the launch version in parentheses at the top right of Search, Ingests,
  About and Preferences. Reopening must not duplicate the label. Use File > Show
  Archive in Explorer to locate the active saved archive folder.
- Drag one message and multiple messages to Explorer. The first gesture prepares
  the export; the next copies an EML or ZIP with exact message bytes, never a shortcut.
- Import the purpose-made PST/OST fixtures without a checkout or external tools.
  Retain any documented partial-conversion evidence and confirm retry adds no
  duplicate messages or source changes.
- Interrupt a test IMAP download. Confirm the yellow Timeout circle has a horizontal
  dash, no traceback or finalizing ETA, and Resume reconnects past committed UIDs.
- Open Preferences from Help. Change Half CPUs, All CPUs and Custom; close with
  the title-bar X while editing a valid count, reopen and confirm persistence.
  Reject zero, fractions and more than twice the CPUs. For a separate source/CLI
  launch with an explicit worker flag, confirm greyed worker controls and the
  command-line note without overwriting the saved preference.
- Check the preview/release choice and automatic launch-check preference survive restart.
  An unavailable updater must explain why; an available installed backend must
  permit a manual check. Decline any proposed installation for this initial test.
- Import three distinctive messages from a test IMAP inbox. Find and display all
  three. Confirm the server's messages and read/unread flags remain untouched.
  Repeating the same import must not add duplicate archived messages.
- Suspend a larger import; keep the app open and search the saved messages.
  Continue and verify progress resumes past saved UIDs without downloading them
  again. Suspend, Quit, relaunch, and choose Continue Import; then repeat using
  Leave import suspended. The incomplete-work prompt appears once per opened
  search window, not whenever a window regains focus.
- During saved-mail work, check Messages requiring processing, completed tasks,
  and distinct per-worker totals. Messages leave the remaining count after their
  final task. Download/import activity has separate labels and counters; the
  removed Workers/Peak workers cards must stay absent.
- Include an encrypted attachment and confirm the original is archived with an
  unscannable result and the import continues. A definition check/update should
  show startup activity without a temporary red failure. Scanner errors must
  retain mail and continue; an actual positive routes to INFECTED1.mbox.
- Open original folders and verify spaces/brackets display as readable labels.
  Open Names and addresses while importing: show loading feedback before results;
  click an evidence count and confirm the matching messages open. Rename an
  identity, restart, and confirm the manual decision survives.
- Open a second search window for the same archive, close the first, and confirm
  import/search remain responsive. Closing the last importing search window must
  explain that Quit suspends import. Quit must dim windows with Shutting down,
  retain resumable work, and exit without a beach ball or hung process.
- Choose File > Reprocess Saved Mail: cancel the long-duration confirmation and
  verify nothing starts. On a disposable archive, confirm a deliberate reprocess
  works. Ordinary app upgrades must not automatically replay completed work.

Windows uses Ctrl shortcuts and its own title-bar controls. macOS Command-Tab
branding and traffic lights are platform-specific. Existing Windows limitations
remain native ARM64 packages (the x64 package can run under emulation). The authoritative matcher is
not connected on either platform; loading/navigation fixes do not enable it.
A complete updater-driven replacement remains a separate acceptance exercise.

#### Cross-platform regression evidence

The Python host and HTML UI are shared. `test-gui-processing` exercises progress,
window ownership, last-window veto, shutdown, saved-request recovery and worker
settings; `test-timeout-presentation` exercises real TLS interruption and Resume,
including saved legacy timeout display. Windows branch CI runs these browser tests
in Edge, the native Preferences/menu/clipboard/import/OLE probe in WebView2, and
that native probe again from the relocated package. The relocated Outlook gate
uses genuine checked-in fixtures, verifies source hashes and archive integrity,
requires the OST fixture's documented partial receipt and checks retry idempotence.
Record workflow head and results for this candidate; macOS results alone cannot
clear Windows acceptance. Updater-driven replacement, native ARM64 and manual
visual checks above retain their own evidence boundaries.
