# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Install the public certificate shipped beside this test installer helper.
# Request administrator access when launched from an ordinary user session.
# Trust the certificate in Local Machine/Trusted People, never a root CA store.
# Resolve files beside this script regardless of the current working directory.
# Verify the resulting trust entry and leave a readable success or failure message.
# This helper changes certificate trust only; it does not install the application.
$ErrorActionPreference = 'Stop'
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
        $process = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Normal -Wait -PassThru
        exit $process.ExitCode
    }
    $certificatePath = Join-Path $PSScriptRoot 'local-test.cer'
    $certificate = Import-Certificate -FilePath $certificatePath -CertStoreLocation 'Cert:\LocalMachine\TrustedPeople' -ErrorAction Stop
    if (-not $certificate -or -not (Test-Path -LiteralPath ('Cert:\LocalMachine\TrustedPeople\' + $certificate.Thumbprint))) {
        throw 'The certificate was not found in Local Machine/Trusted People after import.'
    }
    Write-Host 'Success: the test certificate is installed in Local Machine > Trusted People.'
    Write-Host 'Now double-click base.msixbundle to install Email Collector Toolkit (ECT).'
    Read-Host 'Press Enter to close' | Out-Null
} catch {
    Write-Host ('Certificate installation failed: ' + $_.Exception.Message) -ForegroundColor Red
    Read-Host 'Press Enter to close' | Out-Null
    exit 1
}
