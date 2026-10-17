<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Windows MSIX packaging

The current payload is x64 private CPython activated through
`pythonw.exe -I -m mailarchiver.desktop_entry`. It contains the shared GUI,
Python modules, plugin/SQL/YAML resources, dependency licenses, ClamAV runtime
and baseline definitions, and WinSparkle. WebView2 remains external; startup
reports a missing/incompatible runtime before creating the webview.

Versions come from `pyproject.toml` through the release mapper. The package
identity is `ECT.PythonReader`, distinct from historical Rust packages. Private
`_pth` configuration excludes checkout and user packages. Outputs and evidence
directories must be new.

## Build and signing

[CLI_REFERENCE.md](CLI_REFERENCE.md#windows-packaging) owns the Makefile command
syntax. The payload builder is `scripts/win/build_windows_msix.ps1`; bundling
and signing are separate steps. The Python-reader reusable workflow supplies
the payload to the MSIX workflow for assembly and installed acceptance.
Ordinary pushes do not build installers; dispatch or explicit candidate/release
triggers opt in. [DEVOPS.md](DEVOPS.md) owns trigger and publication policy.

Release and candidate builds select [Microsoft public signing](WINDOWS_PUBLIC_SIGNING.md).
Required Azure/GitHub setup must be verified before claiming trusted output.
There is no fallback to development signing. Public ZIPs omit trust helpers.

Development test signing uses the persistent private key from
`MSIX_TEST_CERT_PFX_BASE64`, checked against `scripts/win/test-signing.cer`; it
never generates a substitute key. Only public trust material may accompany the
base test installer. Explicit installation of that certificate in Local Machine /
Trusted People is necessary for a test-signed install; the adjacent helper
requests elevation and does not install the app or change execution policy.
Private PFX material must not enter artifacts.

## Installed acceptance

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
