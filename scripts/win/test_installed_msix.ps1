# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Test the same signed bundle on disposable x64 and ARM64 GitHub Windows VMs.
# Trust only this run's public test certificate and install for the runner user.
# Exercise installed Rust/Python binaries against a synthetic archive and native UI.
# Upgrade using identical payload bytes and verify archive preservation on uninstall.
# Always remove the test package and newly added trust; never change existing trust.
param([Parameter(Mandatory=$true)][string]$BundleDirectory,
      [ValidateSet('x64','arm64')][string]$Architecture,
      [string]$EvidenceDirectory='dist/installed-evidence')
$ErrorActionPreference='Stop'
$evidence = [IO.Path]::GetFullPath($EvidenceDirectory)
New-Item -ItemType Directory -Path $evidence -ErrorAction Stop | Out-Null
Start-Transcript -Path (Join-Path $evidence 'install.log') | Out-Null
$certPath = Join-Path $BundleDirectory 'local-test.cer'
$cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new((Resolve-Path $certPath).Path)
$storePath = 'Cert:/LocalMachine/TrustedPeople/' + $cert.Thumbprint
$addedTrust = $false
$installed = $false
$originalLocalAppData=$env:LOCALAPPDATA
if (Get-AppxPackage -Name ECT.LocalTest) { throw 'Existing ECT.LocalTest installation must be preserved; use a clean VM.' }
try {
    if (-not (Test-Path $storePath)) {
        Import-Certificate -FilePath $certPath -CertStoreLocation Cert:/LocalMachine/TrustedPeople | Out-Null
        $addedTrust=$true
    }
    . (Join-Path $PSScriptRoot 'sdk.ps1')
    $sdk = Get-WindowsSdkTools
    foreach ($kind in @('base','upgrade')) {
        $priorFiles=@(Get-ChildItem $evidence -Recurse -File | Where-Object FullName -Match 'synthetic\.mailarchive[\\/]')
        $priorHashes=@($priorFiles | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Path + ':' + $_.Hash })
        $bundle = Join-Path $BundleDirectory "$kind.msixbundle"
        & (Join-Path $sdk 'signtool.exe') verify /pa $bundle
        if ($LASTEXITCODE) { throw 'Bundle signature verification failed' }
        Get-FileHash -LiteralPath $bundle -Algorithm SHA256 | Format-List
        Add-AppxPackage -Path $bundle -ErrorAction Stop
        $installed=$true
        if ($priorFiles.Count) {
            $retainedHashes=@($priorFiles | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Path + ':' + $_.Hash })
            if (Compare-Object $priorHashes $retainedHashes) { throw 'Upgrade changed existing archive bytes' }
        }
        $package=Get-AppxPackage -Name ECT.LocalTest
        if ($package.Architecture.ToString().ToLowerInvariant() -ne $Architecture) { throw 'Wrong installed architecture' }
        if ($kind -eq 'upgrade' -and [version]$package.Version -le $baseVersion) { throw 'Package did not upgrade' }
        $baseVersion=[version]$package.Version
        $location=$package.InstallLocation
        $python=Join-Path $location 'python/python.exe'
        & $python -I (Join-Path $PSScriptRoot 'test_windows_msix.py') $bundle (Join-Path $evidence $kind) --installed-root $location
        if ($LASTEXITCODE) { throw 'Installed helper/search/fixity test failed' }
        $archive=Join-Path $evidence "$kind/synthetic.mailarchive"
        $guiFiles=@(Get-ChildItem $archive -Recurse -File)
        $guiBefore=@($guiFiles | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Path + ':' + $_.Hash })
        $env:LOCALAPPDATA=Join-Path $evidence "$kind/profile"
        $process=Start-Process -FilePath (Join-Path $location 'mailsearch-webview.exe') -ArgumentList @('--archive', ('"'+$archive+'"')) -WindowStyle Hidden -PassThru
        try {
            $deadline=[DateTime]::UtcNow.AddSeconds(45)
            do {
                Start-Sleep -Milliseconds 500
                $process.Refresh()
                if ($process.HasExited) { throw 'Installed GUI exited before opening a window' }
            } until ($process.MainWindowHandle -ne 0 -or [DateTime]::UtcNow -gt $deadline)
            if ($process.MainWindowHandle -eq 0) { throw 'Installed GUI did not open a native window' }
            $process.MainWindowTitle | Set-Content (Join-Path $evidence "$kind-window.txt")
            $null=$process.CloseMainWindow()
            if (-not $process.WaitForExit(15000)) { throw 'Installed GUI did not quit' }
        } finally { if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() } }
        $guiAfter=@($guiFiles | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Path + ':' + $_.Hash })
        if (Compare-Object $guiBefore $guiAfter) { throw 'Native launch changed archive bytes' }
    }
    $archiveFiles=@(Get-ChildItem $evidence -Recurse -File | Where-Object FullName -Match 'synthetic\.mailarchive[\\/]')
    $before=@($archiveFiles | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Path + ':' + $_.Hash })
    Remove-AppxPackage -Package $package.PackageFullName -ErrorAction Stop
    $installed=$false
    if (Get-AppxPackage -Name ECT.LocalTest) { throw 'Package remains installed' }
    $after=@($archiveFiles | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Path + ':' + $_.Hash })
    if (Compare-Object $before $after) { throw 'Uninstall changed archive bytes' }
    'Install, native launch, helper, search, upgrade and uninstall passed.' | Set-Content (Join-Path $evidence 'success.txt')
} finally {
    try {
        if ($installed) { Get-AppxPackage -Name ECT.LocalTest | Remove-AppxPackage }
    } finally {
        try { if ($addedTrust) { Remove-Item -LiteralPath $storePath } }
        finally {
            $env:LOCALAPPDATA=$originalLocalAppData
            $cert.Dispose()
            Stop-Transcript | Out-Null
        }
    }
}
