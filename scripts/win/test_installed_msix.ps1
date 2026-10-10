# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Test the same signed bundle on disposable x64 GitHub Windows VMs.
# Trust only this run's public test certificate and install for the runner user.
# Exercise the frozen Python reader, native Close and Quit on synthetic mail.
# Upgrade using identical payload bytes and verify archive preservation on uninstall.
# Always remove the test package and newly added trust; never change existing trust.
param([Parameter(Mandatory=$true)][string]$BundleDirectory,
      [ValidateSet('x64')][string]$Architecture,
      [string]$EvidenceDirectory='dist/installed-evidence')
$ErrorActionPreference='Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'NativeMenu.cs') -ReferencedAssemblies Accessibility
$evidence = [IO.Path]::GetFullPath($EvidenceDirectory)
New-Item -ItemType Directory -Path $evidence -ErrorAction Stop | Out-Null
Start-Transcript -Path (Join-Path $evidence 'install.log') | Out-Null
$certPath = Join-Path $BundleDirectory 'local-test.cer'
$cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new((Resolve-Path $certPath).Path)
$storePath = 'Cert:/LocalMachine/TrustedPeople/' + $cert.Thumbprint
$addedTrust = $false
$installed = $false
$originalLocalAppData=$env:LOCALAPPDATA
$originalAppData=$env:APPDATA
if (Get-AppxPackage -Name ECT.PythonReader) { throw 'Existing ECT.PythonReader installation must be preserved; use a clean VM.' }
# Activate the registered desktop application with its package identity.
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
[ComImport, Guid("2e941141-7f97-4756-ba1d-9decde894a3d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IECTActivationManager {
    void ActivateApplication([MarshalAs(UnmanagedType.LPWStr)] string appId,
        [MarshalAs(UnmanagedType.LPWStr)] string arguments, uint options, out uint processId);
    void ActivateForFile(IntPtr appId, IntPtr items, IntPtr verb, out uint processId);
    void ActivateForProtocol(IntPtr appId, IntPtr items, out uint processId);
}
public static class ECTActivation {
    public static uint Launch(string appId, string arguments) {
        object manager = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C")));
        try {
            uint processId;
            ((IECTActivationManager)manager).ActivateApplication(appId, arguments, 0, out processId);
            return processId;
        } finally { Marshal.ReleaseComObject(manager); }
    }
}
"@
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
        $package=Get-AppxPackage -Name ECT.PythonReader
        if ($package.Architecture.ToString().ToLowerInvariant() -ne $Architecture) { throw 'Wrong installed architecture' }
        if ($kind -eq 'upgrade' -and [version]$package.Version -le $baseVersion) { throw 'Package did not upgrade' }
        $baseVersion=[version]$package.Version
        $location=$package.InstallLocation
        $appId=$package.PackageFamilyName + '!ECT'
        $startEntry=Get-StartApps | Where-Object AppID -EQ $appId
        if ($startEntry.Name -ne 'Email Collection Toolkit (Python Preview)') { throw 'Expected Start menu application name is missing' }
        $testOutput=Join-Path $evidence $kind
        $testPid=[ECTActivation]::Launch($appId, ('--msix-test "'+$testOutput+'"'))
        $testProcess=Get-Process -Id $testPid -ErrorAction SilentlyContinue
        if ($testProcess -and -not $testProcess.WaitForExit(120000)) {
            $testProcess.Kill()
            throw 'Packaged self-test timed out'
        }
        $testLog=$testOutput + '.log'
        if (Test-Path $testLog) { Get-Content $testLog }
        $report=Join-Path $testOutput 'report.json'
        if (-not (Test-Path $report)) { throw 'Packaged self-test did not produce its success report' }
        if (-not (Get-Content $report -Raw | ConvertFrom-Json).installed_msix_tested) { throw 'Self-test did not exercise installed package' }
        $archive=Join-Path $evidence "$kind/synthetic.mailarchive"
        $guiFiles=@(Get-ChildItem $archive -Recurse -File)
        $guiBefore=@($guiFiles | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Path + ':' + $_.Hash })
        $env:LOCALAPPDATA=Join-Path $evidence "$kind/profile"
        $env:APPDATA=$env:LOCALAPPDATA
        foreach ($closeAction in @('close','quit')) {
            $guiPid=[ECTActivation]::Launch($appId, ('--archive "'+$archive+'"'))
            $process=Get-Process -Id $guiPid
            try {
                $deadline=[DateTime]::UtcNow.AddSeconds(45)
                do {
                    Start-Sleep -Milliseconds 500
                    $process.Refresh()
                    if ($process.HasExited) { throw 'Installed GUI exited before opening a window' }
                } until ($process.MainWindowHandle -ne 0 -or [DateTime]::UtcNow -gt $deadline)
                if ($process.MainWindowHandle -eq 0) { throw 'Installed GUI did not open a native window' }
                $process.MainWindowTitle | Set-Content (Join-Path $evidence "$kind-$closeAction-window.txt")
                if ($closeAction -eq 'close') {
                    if (-not $process.CloseMainWindow()) { throw 'Installed GUI rejected ordinary Close' }
                } else {
                    # WinForms MenuStrip exposes MSAA even when UIA omits its items.
                    $invoked=$false
                    $quitDeadline=[DateTime]::UtcNow.AddSeconds(10)
                    do {
                        $invoked=[ECTNativeMenu]::InvokeQuit($process.MainWindowHandle)
                        if (-not $invoked) { Start-Sleep -Milliseconds 100 }
                    } until ($invoked -or [DateTime]::UtcNow -gt $quitDeadline)
                    if (-not $invoked) { throw 'Installed Python GUI File/Quit command is missing' }
                }
                if (-not $process.WaitForExit(15000)) { throw 'Installed GUI did not quit' }
            } finally { if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() } }
        }
        $guiAfter=@(Get-ChildItem $archive -Recurse -File | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Path + ':' + $_.Hash })
        if (Compare-Object $guiBefore $guiAfter) { throw 'Native launch changed archive bytes' }
    }
    $archiveFiles=@(Get-ChildItem $evidence -Recurse -File | Where-Object FullName -Match 'synthetic\.mailarchive[\\/]')
    $before=@($archiveFiles | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Path + ':' + $_.Hash })
    Remove-AppxPackage -Package $package.PackageFullName -ErrorAction Stop
    $installed=$false
    if (Get-AppxPackage -Name ECT.PythonReader) { throw 'Package remains installed' }
    $after=@($archiveFiles | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Path + ':' + $_.Hash })
    if (Compare-Object $before $after) { throw 'Uninstall changed archive bytes' }
    'Install, native launch, Python search/completion, upgrade and uninstall passed.' | Set-Content (Join-Path $evidence 'success.txt')
} finally {
    try {
        if ($installed) { Get-AppxPackage -Name ECT.PythonReader | Remove-AppxPackage }
    } finally {
        try { if ($addedTrust) { Remove-Item -LiteralPath $storePath } }
        finally {
            $env:LOCALAPPDATA=$originalLocalAppData
            $env:APPDATA=$originalAppData
            $cert.Dispose()
            Stop-Transcript | Out-Null
        }
    }
}
