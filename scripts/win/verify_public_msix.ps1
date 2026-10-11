# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify Microsoft-signed bundles before exposing them as release artifacts.
# Windows SignTool authenticates every signature against the platform trust store.
# Missing timestamps produce warnings, which fail this gate like signature errors.
# Bundle identity must match the exact approved certificate subject supplied by CI.
# Hash evidence connects Windows verification to cross-platform release staging.
# No certificate is imported and no private signing material is handled here.
param([string]$BundleDirectory='dist/bundle',
      [Parameter(Mandatory=$true)][string]$Publisher)
$ErrorActionPreference='Stop'
if ([string]::IsNullOrWhiteSpace($Publisher) -or $Publisher -eq 'CN=ECT Local Test') {
    throw 'The approved public certificate subject is required.'
}
. (Join-Path $PSScriptRoot 'sdk.ps1')
$sdk = Get-WindowsSdkTools
foreach ($kind in @('base','upgrade')) {
    $bundle = Join-Path $BundleDirectory "$kind.msixbundle"
    & (Join-Path $sdk 'signtool.exe') verify /pa /all /v /tw $bundle
    if ($LASTEXITCODE -ne 0) { throw 'Public trust or timestamp verification failed.' }
    $zip = [IO.Compression.ZipFile]::OpenRead((Resolve-Path $bundle).Path)
    try {
        $reader = [IO.StreamReader]::new($zip.GetEntry('AppxMetadata/AppxBundleManifest.xml').Open())
        try { $identity = ([xml]$reader.ReadToEnd()).Bundle.Identity } finally { $reader.Dispose() }
        if ($identity.Publisher -cne $Publisher -or $identity.Name -ne 'ECT.PythonReader') {
            throw 'Signed bundle publisher or package name differs from the expected identity.'
        }
    } finally { $zip.Dispose() }
}
$base = Join-Path $BundleDirectory 'base.msixbundle'
[ordered]@{
    signing_mode = 'public-trust'
    publisher = $Publisher
    base_sha256 = (Get-FileHash -LiteralPath $base -Algorithm SHA256).Hash.ToLowerInvariant()
    timestamp_required = $true
} | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $BundleDirectory 'signing-verification.json')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'MSIX_PUBLIC_README.txt') -Destination (Join-Path $BundleDirectory 'README.txt')
@(Get-Item $base, (Join-Path $BundleDirectory 'signing-verification.json')) | Get-FileHash -Algorithm SHA256 | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $BundleDirectory 'sha256.json')
