# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Sign test MSIX packages with the persistent repository Actions secret.
# Pin its public certificate to the checked-in test identity before signing.
# Never generate replacement keys when the secret is missing or invalid.
# Keep temporary private key material beside private build intermediates only.
# Remove that material on success and failure; export only the public certificate.
# Testers explicitly trust this identity once, until expiry or deliberate rotation.
param([Parameter(Mandatory=$true)][string]$Package)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'sdk.ps1')
if (-not $env:MSIX_TEST_CERT_PFX_BASE64) { throw 'MSIX_TEST_CERT_PFX_BASE64 is required; no replacement test certificate will be generated.' }
$directory = Split-Path (Resolve-Path -LiteralPath $Package) -Parent
$pfx = Join-Path $directory 'local-test.pfx'
if (Test-Path -LiteralPath $pfx) { throw 'Test signing key already exists; preserve it.' }
$cert = $null
$expected = $null
$createdKey = $false
try {
    $bytes = [Convert]::FromBase64String($env:MSIX_TEST_CERT_PFX_BASE64)
    $cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new($bytes, '', [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet)
    $expected = [Security.Cryptography.X509Certificates.X509Certificate2]::new((Join-Path $PSScriptRoot 'test-signing.cer'))
    if (-not $cert.HasPrivateKey -or $cert.GetCertHashString('SHA256') -ne $expected.GetCertHashString('SHA256')) {
        throw 'The signing secret does not match the pinned test certificate/private key.'
    }
    if ($cert.NotBefore.ToUniversalTime() -gt [DateTime]::UtcNow -or $cert.NotAfter.ToUniversalTime() -le [DateTime]::UtcNow) { throw 'The persistent test certificate is outside its validity period.' }
    $createdKey = $true
    [IO.File]::WriteAllBytes($pfx, $bytes)
    [IO.File]::WriteAllBytes((Join-Path $directory 'local-test.cer'), $cert.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
    & (Join-Path (Get-WindowsSdkTools) 'signtool.exe') sign /fd SHA256 /f $pfx $Package
    if ($LASTEXITCODE) { throw 'Test package signing failed' }
    Get-FileHash -LiteralPath $Package -Algorithm SHA256
} finally {
    if ($createdKey -and (Test-Path -LiteralPath $pfx)) { Remove-Item -LiteralPath $pfx -Force }
    if ($cert) { $cert.Dispose() }
    if ($expected) { $expected.Dispose() }
}
