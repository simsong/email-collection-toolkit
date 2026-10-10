Email Collection Toolkit - Windows alpha installer

1. Extract this entire ZIP into a folder.
2. Right-click Install-Test-Certificate.ps1 and choose Run with PowerShell.
   On Windows 11, you may first need Show more options.
   Approve the administrator prompt. Wait for the success message, then press Enter.
   The script installs local-test.cer into Local Machine > Trusted People.
   It does not install the application or change your saved execution policy.
   This certificate is reused across test builds until October 7, 2028.
   Install it once; reinstall only after an announced certificate rotation.
   Do not use the certificate wizard defaults: Current User is insufficient.

   If Run with PowerShell is unavailable or script execution is blocked, open
   PowerShell in the extracted folder and run:

   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-Test-Certificate.ps1

3. Double-click base.msixbundle and choose Install.
   This package contains the x64 Python reader.
4. Launch Email Collection Toolkit (Python Preview) from the Start menu.

If double-click installation is unavailable, open an ordinary PowerShell
window in the extracted folder and run:

   Add-AppxPackage -Path .\base.msixbundle

If installation still fails, copy the full error message and error code.

WebView2 Runtime must already be installed. It is not included in this ZIP.
Microsoft provides the runtime and a standalone installer for offline machines:
https://developer.microsoft.com/microsoft-edge/webview2/#download
For a disconnected machine, transfer the appropriate standalone installer
from a connected computer and install it before running ECT.

Archive creation, importing, reading and search use the shared Python services
and webview interface. The package bundles ClamAV; virus definitions are stored
under the user's Local AppData, outside .mailarchive folders. This preview has
a separate package identity and does not replace the historical Rust preview.
WinSparkle provides Check for Updates and Update Settings in the Help menu.
A complete updater-driven Python package upgrade has not yet been validated,
and the published feed has no Python Windows release; install signed previews
manually until a compatible update is published.

Files: base.msixbundle is the only installer; local-test.cer is its public
certificate; sha256.json contains checksums. No upgrade fixture is needed
for manual installation. This alpha uses an explicitly trusted test certificate.
