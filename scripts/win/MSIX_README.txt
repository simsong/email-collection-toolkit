Email Collection Toolkit - Windows test installer

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
   Windows selects the x64 or ARM64 application automatically.
4. Launch Email Collector Toolkit (ECT) from the Start menu.

If double-click installation is unavailable, open an ordinary PowerShell
window in the extracted folder and run:

   Add-AppxPackage -Path .\base.msixbundle

If installation still fails, copy the full error message and error code.

WebView2 Runtime must already be installed. It is not included in this ZIP.
Microsoft provides the runtime and a standalone installer for offline machines:
https://developer.microsoft.com/microsoft-edge/webview2/#download
For a disconnected machine, transfer the appropriate standalone installer
from a connected computer and install it before running ECT.

This is a reader test build; Windows importing is not yet enabled.
See the associated GitHub Actions run for this build's installation test results.

Files: base.msixbundle is the only installer; local-test.cer is its public
certificate; sha256.json contains checksums. No upgrade fixture is needed
for manual installation. Production distribution will use trusted signing.
