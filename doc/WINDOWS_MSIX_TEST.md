<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Local Windows MSIX prototype

This isolated prototype starts at PR #153 commit `e1b4170`. It does not modify
the Mac's branch or publish a release. The selected distribution is MSIX;
WebView2 remains a separately installed prerequisite, never bundled or downloaded
by ECT. Missing runtime detection uses a native dialog before webview creation.

## Automated build and test

On this ARM64 Windows VM, from the checkout root:

```powershell
cargo msix-test
$package = Get-ChildItem dist/windows-msix-test/*.msix | Select-Object -First 1
pwsh -File scripts/win/sign_test_msix.ps1 -Package $package.FullName
.venv/Scripts/python.exe scripts/win/test_windows_msix.py $package.FullName dist/msix-validation
```

The Cargo alias invokes `scripts/win/build_windows_msix.ps1`, which synchronizes the
locked Python dependencies, builds both Rust executables, stages private CPython,
Python application code and resources, generates a manifest, and runs MakeAppx.
The optional `-RustBinaryDirectory` argument reuses prebuilt executables for local
iteration. Output directories must be new; prior artifacts are not deleted.
`make msix-test` and `make test-msix MSIX_PACKAGE=... MSIX_EVIDENCE=...` wrap these
operations when Make is available. The latter evidence directory must be new.

The private interpreter is a complete CPython runtime rather than a copied venv.
An isolated `python312._pth` keeps imports inside the package. Rust discovers
`python/python.exe` beside its executable, avoiding checkout paths and requiring
no Python installation on the destination. Package versions encode the existing
shared release mapper's build number in four 16-bit fields; this is a local-test
identity and version scheme, not a finalized Store/release identity.

The signing script creates a 30-day self-signed code-signing certificate in the
ignored output directory. It does not install trust or install the MSIX. Keep
`local-test.pfx` private; only `local-test.cer` is suitable for transfer to a test
machine. Normal installation requires explicitly trusting that test certificate.
Production requires the separately agreed trusted signing service.

## Evidence and limitations

The first ARM64 MSIX passed MakeAppx validation and SignTool signing. Its actual
contents were extracted to another directory and tested using the private Python
runtime. Rust-to-Python capabilities, staged search completion, installed-runtime
probing and unchanged synthetic archive hashes passed. The extracted executable
opened a native Windows window and closed cleanly. Evidence is retained in
`dist/msix-validation/report.json` and `native-window.json`.

These checks do not establish installed-MSIX activation, upgrade/uninstall,
x64 support, offline certificate trust or the missing-runtime dialog on a machine
without WebView2. Those remain acceptance work. The manifest's Windows 10 build
19041 minimum is a prototype setting, not a tested support promise.

Windows writing/imports remain unsupported and disabled. ClamAV binaries,
definitions, and external converters are not included in this reader prototype;
their native Windows port and packaging remain necessary for full application
parity. Packaging the Python helper does not enable those capabilities.

Ruff passed on the final source. GNU Make is unavailable on this VM. Scoped
Pylint reports the platform-only `fcntl` import as unavailable on Windows;
no suppression was added and no complete lint/CI pass is claimed. No public
release, branch push, certificate trust change or package installation occurred.

## MSIX installation matrix (2026-10-07)

The MSIX decision supersedes the older shared WinSparkle feed/EXE plan.
`windows-msix.yml` supports explicit dispatch and reusable release calls; ordinary
pushes do not run it. Two native build jobs produce x64 and ARM64 payloads once.
One assembly job creates a signed common bundle and a higher-version upgrade
fixture with identical application bytes. Both installation VMs download that
same artifact: Windows Server x64 (`windows-latest`) and Windows 11 ARM64
(`windows-11-arm`). Installation jobs do not rebuild. Private signing keys remain
outside uploaded artifacts. Test packages are never published as release assets.
The release caller waits for this gate after tag preflight. Windows 10 testing
is not required. No GitHub Team or AWS provisioning is needed.

Installed checks exercise private Python discovery, synthetic search/fixity,
a native window, upgrade and uninstall, removing test packages and added trust
in cleanup. WebView2 remains external and its absence fails this positive test.
Its writable user-data directory is outside the immutable package. Hosted
execution is pending; earlier local prototype results do not validate this head.
Start-menu activation, missing-runtime UI, Windows imports/scanner/converters,
and physical interaction remain separate acceptance gaps.

Before the new workflow is available for dispatch, an explicit `[msix-ci]` commit
message on `codex/windows-msix-ci` starts the same run. Other push messages skip
payload building; this exception is limited to the packaging development branch.

The downloadable `windows-msix-install-test` artifact contains exactly one installer,
`base.msixbundle`, plus its public test certificate and checksum inventory. The
higher-version bundle is isolated in `ci-only-msix-upgrade-fixture`; installation
CI downloads both artifacts into the same directory to retain upgrade coverage.
Users downloading the installer do not need the upgrade fixture.

The installer artifact includes README.txt with certificate installation into
Local Machine/Trusted People using elevated PowerShell (Current User trust is
insufficient), installation and launch steps, and external WebView2 guidance.

The test installer ZIP includes `Install-Test-Certificate.ps1`: right-click Run
with PowerShell requests elevation, installs the adjacent public certificate in
Local Machine/Trusted People, verifies its presence, and displays the outcome.
The README documents this path and a command fallback without changing the
machine execution policy. The helper does not install the application.

Windows installation registers the display name **Email Collector Toolkit (ECT)**.
The installation matrix verifies that Start menu entry and activates its app ID
using IApplicationActivationManager. The activated Rust executable runs the
bundled Python self-test in package context, then CI activates the native reader
on the synthetic archive before testing upgrade and uninstall. Taskbar pinning
is a user choice, not an installation requirement.

Native installed-package evidence: GitHub Actions run 37677162147 passed all
five jobs on commit `4912379`: both payload builds, bundle assembly, and install/
registered activation/private helper/search/native window/upgrade/uninstall
checks on Windows Server x64 and Windows 11 ARM64. The same bundle was tested
on both machines; synthetic archive fixity checks passed. Ordinary CI run
37677162905 also passed. This does not establish Windows import support or
resolve the initial archive-picker UX.

Windows test signing now requires the persistent `MSIX_TEST_CERT_PFX_BASE64`
Actions secret, passed only to bundle signing (including reusable release calls).
The signer checks it against `scripts/win/test-signing.cer`, requires a private
key and current validity, and removes temporary PFX material on success/failure.
No fallback certificate is generated. This supersedes the ephemeral test-key
policy; testers trust the public certificate once until expiration (2028-10-07)
or deliberate rotation. Production trusted signing remains separate.
