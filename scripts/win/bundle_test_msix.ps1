# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Combine architecture payloads built once into a shared test MSIX bundle.
# Base and upgrade fixtures reuse identical application bytes with mapped versions.
# Sign both bundles using the persistent, pinned repository test identity.
# Export only signed bundles and the public certificate to the CI artifact directory.
# The private signing key stays outside that directory and never becomes an artifact.
param([Parameter(Mandatory=$true)][string]$InputDirectory,
      [string]$OutputDirectory='dist/bundle')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'sdk.ps1')
$sdk = Get-WindowsSdkTools
New-Item -ItemType Directory -Path $OutputDirectory -ErrorAction Stop | Out-Null
$work = Join-Path $OutputDirectory '../bundle-private'
New-Item -ItemType Directory -Path $work -ErrorAction Stop | Out-Null
foreach ($kind in @('base','upgrade')) {
    $payload = Join-Path $work $kind
    New-Item -ItemType Directory -Path $payload | Out-Null
    foreach ($arch in @('x64','arm64')) {
        $pattern = if ($kind -eq 'base') { "$arch/*.msix" } else { "$arch/upgrade/*.msix" }
        $files = @(Get-ChildItem (Join-Path $InputDirectory $pattern))
        if ($files.Count -ne 1) { throw "Expected one $kind package for $arch" }
        Copy-Item -LiteralPath $files[0].FullName -Destination (Join-Path $payload "$arch.msix")
    }
    $bundle = Join-Path $work "$kind.msixbundle"
    $versions = foreach ($arch in @('x64','arm64')) {
        $zip = [IO.Compression.ZipFile]::OpenRead((Join-Path $payload "$arch.msix"))
        try {
            $reader = [IO.StreamReader]::new($zip.GetEntry('AppxManifest.xml').Open())
            try { ([xml]$reader.ReadToEnd()).Package.Identity.Version } finally { $reader.Dispose() }
        } finally { $zip.Dispose() }
    }
    if ($versions[0] -ne $versions[1]) { throw 'Architecture package versions differ' }
    & (Join-Path $sdk 'makeappx.exe') bundle /bv $versions[0] /d $payload /p $bundle *> (Join-Path $work "$kind.log")
    if ($LASTEXITCODE) { throw 'Bundle creation failed' }
}
& (Join-Path $PSScriptRoot 'sign_test_msix.ps1') -Package (Join-Path $work 'base.msixbundle')
& (Join-Path $PSScriptRoot 'sign_test_msix.ps1') -Package (Join-Path $work 'upgrade.msixbundle')
foreach ($name in @('base.msixbundle','upgrade.msixbundle','local-test.cer')) {
    Copy-Item -LiteralPath (Join-Path $work $name) -Destination $OutputDirectory
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'MSIX_README.txt') -Destination (Join-Path $OutputDirectory 'README.txt')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Install-Test-Certificate.ps1') -Destination $OutputDirectory
Get-Item (Join-Path $OutputDirectory 'base.msixbundle'), (Join-Path $OutputDirectory 'local-test.cer') | Get-FileHash -Algorithm SHA256 | ConvertTo-Json | Set-Content (Join-Path $OutputDirectory 'sha256.json')
