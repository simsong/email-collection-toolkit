# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Assemble the Python desktop alpha package using the Windows SDK.
# PyInstaller bundles CPython and locked runtime dependencies together.
# The frozen entry point excludes checkout paths and user-installed modules.
# Assembly never installs certificates or packages; separate CI validates those.
# WebView2 remains a platform prerequisite for the Python webview.
# Native writer/scanner/converter parity is deliberately not claimed by packaging.
param(
    [ValidateSet('arm64','x64')][string]$Architecture = 'arm64',
    [switch]$CreateUpgradeTest,
    [string]$OutputDirectory = 'dist/windows-msix-test',
    [string]$SdkDirectory
)
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location -LiteralPath $root
. (Join-Path $PSScriptRoot 'sdk.ps1')
if (-not $SdkDirectory) { $SdkDirectory = Get-WindowsSdkTools }
& uv sync --locked --no-dev --group packaging --python 3.12
if ($LASTEXITCODE) { throw 'Locked Python dependency setup failed' }
$python = Join-Path $root '.venv/Scripts/python.exe'
$machine = & $python -c 'import platform; print(platform.machine())'
$expectedMachine = if ($Architecture -eq 'arm64') { 'ARM64' } else { 'AMD64' }
if ($machine -ne $expectedMachine) { throw 'Python architecture does not match the package architecture.' }
$metadata = & $python -c 'import json,sys,tomllib; from pathlib import Path; from mailarchiver.release_versions import release_metadata; v=tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]; tag,channel,build,display=release_metadata(v); print(json.dumps({"home":sys.base_prefix,"version":".".join(str((build >> n) & 65535) for n in (48,32,16,0)),"display":display,"upgrade":".".join(str(((build + 1) >> n) & 65535) for n in (48,32,16,0))}))' | ConvertFrom-Json
if ($LASTEXITCODE) { throw 'Release metadata failed' }
if (-not [IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory = Join-Path $root $OutputDirectory }
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Use a new output directory; existing packages are preserved.' }
$stage = Join-Path $OutputDirectory 'stage'
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
& $python -m PyInstaller --noconfirm --windowed --onedir --name ect --distpath (Join-Path $OutputDirectory 'frozen') --workpath (Join-Path $OutputDirectory 'work') --specpath $OutputDirectory --copy-metadata mailarchiver --collect-data mailarchiver --add-data "$root/src/mailarchiver/plugins;mailarchiver/plugins" --hidden-import mailarchiver.processing.builtin --collect-all webview --hidden-import webview.platforms.winforms --hidden-import webview.platforms.edgechromium --add-data "$root/gui;gui" --exclude-module pypff scripts/desktop_entry.py
if ($LASTEXITCODE) { throw 'Python GUI freezing failed' }
Move-Item -LiteralPath (Join-Path $OutputDirectory 'frozen/ect') -Destination $stage
foreach ($name in @('LICENSE','COPYRIGHT','THIRD_PARTY_NOTICES.md')) {
    Copy-Item -LiteralPath (Join-Path $root $name) -Destination $stage
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'test_windows_msix.py') -Destination $stage
Copy-Item -LiteralPath (Join-Path $root 'licenses') -Destination $stage -Recurse
New-Item -ItemType Directory -Path (Join-Path $stage 'Assets') | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'gui/icons/rainbow-post-48.png') -Destination (Join-Path $stage 'Assets/Logo.png')
Copy-Item -LiteralPath (Join-Path $root 'gui/icons/rainbow-post-192.png') -Destination (Join-Path $stage 'Assets/Tile.png')
$manifest = @"
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10" xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10" xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities" IgnorableNamespaces="uap rescap">
 <Identity Name="ECT.LocalTest" Publisher="CN=ECT Local Test" Version="$($metadata.version)" ProcessorArchitecture="$Architecture" />
 <Properties><DisplayName>Email Collector Toolkit (ECT)</DisplayName><PublisherDisplayName>ECT Local Test</PublisherDisplayName><Logo>Assets/Logo.png</Logo></Properties>
 <Dependencies><TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" /></Dependencies>
 <Resources><Resource Language="en-us" /></Resources>
 <Applications><Application Id="ECT" Executable="ect.exe" EntryPoint="Windows.FullTrustApplication"><uap:VisualElements DisplayName="Email Collector Toolkit (ECT)" Description="Python desktop test package" BackgroundColor="transparent" Square150x150Logo="Assets/Tile.png" Square44x44Logo="Assets/Logo.png" /></Application></Applications>
 <Capabilities><rescap:Capability Name="runFullTrust" /></Capabilities>
</Package>
"@
[IO.File]::WriteAllText((Join-Path $stage 'AppxManifest.xml'), $manifest, [Text.UTF8Encoding]::new($false))
$package = Join-Path $OutputDirectory "ECT-$($metadata.display)-$Architecture-test.msix"
& (Join-Path $SdkDirectory 'makeappx.exe') pack /d $stage /p $package *> (Join-Path $OutputDirectory 'makeappx.log')
if ($LASTEXITCODE) { throw 'MakeAppx validation/packaging failed' }
Get-FileHash -Algorithm SHA256 -LiteralPath $package | Format-List
Write-Output "Unsigned local test MSIX: $package"
Write-Output 'Not installed. A trusted package signature is required before normal installation.'

if ($CreateUpgradeTest) {
    $upgradeDirectory = Join-Path $OutputDirectory 'upgrade'
    New-Item -ItemType Directory -Path $upgradeDirectory | Out-Null
    $upgradeManifest = $manifest.Replace('Version="' + $metadata.version + '"', 'Version="' + $metadata.upgrade + '"')
    [IO.File]::WriteAllText((Join-Path $stage 'AppxManifest.xml'), $upgradeManifest, [Text.UTF8Encoding]::new($false))
    & (Join-Path $SdkDirectory 'makeappx.exe') pack /d $stage /p (Join-Path $upgradeDirectory "$Architecture.msix") *> (Join-Path $OutputDirectory 'makeappx-upgrade.log')
    if ($LASTEXITCODE) { throw 'Upgrade fixture packaging failed' }
}
