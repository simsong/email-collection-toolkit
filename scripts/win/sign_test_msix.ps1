# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Sign a development MSIX with a new local-only certificate and private key.
# This certificate identifies test artifacts and is not a public release identity.
# Keep the private PFX in ignored build output and distribute only the public CER.
# No certificate store, machine policy, or installed application is changed.
# Normal installation requires a separate explicit trust decision on the test VM.
param([Parameter(Mandatory=$true)][string]$Package)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'sdk.ps1')
$directory = Split-Path (Resolve-Path -LiteralPath $Package) -Parent
$pfx = Join-Path $directory 'local-test.pfx'
if (Test-Path -LiteralPath $pfx) { throw 'Test signing key already exists; preserve it.' }
$rsa = [Security.Cryptography.RSA]::Create(3072)
$request = [Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=ECT Local Test', $rsa, [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
$oids = [Security.Cryptography.OidCollection]::new()
$null = $oids.Add([Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.3'))
$request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($oids, $true))
$request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new([Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature, $true))
$cert = $request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddMinutes(-5), [DateTimeOffset]::UtcNow.AddDays(30))
[IO.File]::WriteAllBytes($pfx, $cert.Export([Security.Cryptography.X509Certificates.X509ContentType]::Pfx))
[IO.File]::WriteAllBytes((Join-Path $directory 'local-test.cer'), $cert.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
& (Join-Path (Get-WindowsSdkTools) 'signtool.exe') sign /fd SHA256 /f $pfx $Package
if ($LASTEXITCODE) { throw 'Test package signing failed' }
Get-FileHash -LiteralPath $Package -Algorithm SHA256
$cert.Dispose()
$rsa.Dispose()
