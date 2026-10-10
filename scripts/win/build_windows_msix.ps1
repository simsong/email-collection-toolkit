# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Assemble the x64 Python webview reader preview package using the Windows SDK.
# The private CPython installation and locked runtime dependencies travel together.
# Python's isolated path file excludes checkout paths and user-installed modules.
# Assembly never installs certificates or packages; separate CI validates those.
# WebView2 remains external; the Python entry point diagnoses a missing runtime.
# The native scanner and baseline definitions must be present in every package.
param(
    [ValidateSet('x64')][string]$Architecture = 'x64',
    [switch]$CreateUpgradeTest,
    [string]$PythonEnvironment,
    [string]$ClamavDirectory = '.tmp/clamav-x64',
    [string]$WinSparkleDirectory = '.tmp/winsparkle',
    [string]$DefinitionsDirectory = 'etc/clamdb',
    [string]$OutputDirectory = 'dist/windows-msix-test',
    [string]$SdkDirectory
)
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location -LiteralPath $root
if (-not (Test-Path -LiteralPath (Join-Path $WinSparkleDirectory 'WinSparkle.dll') -PathType Leaf)) {
    throw 'Missing WinSparkle runtime; run make prepare-windows-updater first.'
}
foreach ($name in @('libclamav.dll', 'freshclam.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ClamavDirectory $name) -PathType Leaf)) {
        throw "Missing Windows ClamAV runtime file: $ClamavDirectory/$name"
    }
}
foreach ($name in @('main', 'daily', 'bytecode')) {
    $databases = @('.cvd', '.cld') | Where-Object {
        Test-Path -LiteralPath (Join-Path $DefinitionsDirectory ($name + $_)) -PathType Leaf
    }
    if (@($databases).Count -ne 1) { throw "Expected one $name.cvd or $name.cld in $DefinitionsDirectory" }
}
. (Join-Path $PSScriptRoot 'sdk.ps1')
if (-not $SdkDirectory) { $SdkDirectory = Get-WindowsSdkTools }
if (-not $PythonEnvironment) {
    $PythonEnvironment = Join-Path $root '.tmp/msix-runtime'
    $previousEnvironment = $env:UV_PROJECT_ENVIRONMENT
    try {
        $env:UV_PROJECT_ENVIRONMENT = $PythonEnvironment
        & uv sync --locked --no-dev --python cpython-3.12-windows-x86_64-none
        if ($LASTEXITCODE) { throw 'Locked x64 Python dependency setup failed' }
    } finally { $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment }
}
$python = Join-Path $PythonEnvironment 'Scripts/python.exe'
$machine = & $python -c 'import sysconfig; print(sysconfig.get_platform())'
if ($LASTEXITCODE -or $machine -ne 'win-amd64') { throw 'Python must be the x64 build (including on ARM hosts).' }
$metadata = & $python -c 'import json,sys,tomllib; from pathlib import Path; from mailarchiver.release_versions import release_metadata; v=tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]; tag,channel,build,display=release_metadata(v); print(json.dumps({"home":sys.base_prefix,"version":".".join(str((build >> n) & 65535) for n in (48,32,16,0)),"display":display,"upgrade":".".join(str(((build + 1) >> n) & 65535) for n in (48,32,16,0))}))' | ConvertFrom-Json
if ($LASTEXITCODE) { throw 'Release metadata failed' }
if (-not [IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory = Join-Path $root $OutputDirectory }
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Use a new output directory; existing packages are preserved.' }
$stage = Join-Path $OutputDirectory 'stage'
$privatePython = Join-Path $stage 'python'
New-Item -ItemType Directory -Path $privatePython -Force | Out-Null
foreach ($name in @('python.exe','pythonw.exe','python3.dll','python312.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt','Lib','DLLs')) {
    Copy-Item -LiteralPath (Join-Path $metadata.home $name) -Destination $privatePython -Recurse
}
$site = Join-Path $privatePython 'Lib/site-packages'
New-Item -ItemType Directory -Path $site -Force | Out-Null
Get-ChildItem (Join-Path $PythonEnvironment 'Lib/site-packages') | Where-Object { $_.Name -notlike '*.pth' -and $_.Name -ne '__pycache__' } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $site -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $root 'src/mailarchiver') -Destination $site -Recurse
[IO.File]::WriteAllText((Join-Path $privatePython 'python312._pth'), ".`nLib`nDLLs`nLib/site-packages`n", [Text.UTF8Encoding]::new($false))
Copy-Item -LiteralPath (Join-Path $root 'gui') -Destination (Join-Path $privatePython 'Lib/gui') -Recurse
$clamav = Join-Path $privatePython 'clamav'
Copy-Item -LiteralPath $ClamavDirectory -Destination $clamav -Recurse
Copy-Item -LiteralPath $DefinitionsDirectory -Destination (Join-Path $clamav 'definitions') -Recurse
Copy-Item -LiteralPath $WinSparkleDirectory -Destination (Join-Path $privatePython 'winsparkle') -Recurse
foreach ($name in @('LICENSE','COPYRIGHT','THIRD_PARTY_NOTICES.md')) {
    Copy-Item -LiteralPath (Join-Path $root $name) -Destination $stage
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'test_windows_msix.py') -Destination $stage
Copy-Item -LiteralPath (Join-Path $root 'scripts/test_python_reader_native.py') -Destination $stage
Copy-Item -LiteralPath (Join-Path $root 'licenses') -Destination $stage -Recurse
New-Item -ItemType Directory -Path (Join-Path $stage 'Assets') | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'gui/icons/rainbow-post-48.png') -Destination (Join-Path $stage 'Assets/Logo.png')
Copy-Item -LiteralPath (Join-Path $root 'gui/icons/rainbow-post-192.png') -Destination (Join-Path $stage 'Assets/Tile.png')
$manifest = @"
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10" xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10" xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10" xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities" IgnorableNamespaces="uap uap10 rescap">
 <Identity Name="ECT.PythonReader" Publisher="CN=ECT Local Test" Version="$($metadata.version)" ProcessorArchitecture="$Architecture" />
 <Properties><DisplayName>Email Collection Toolkit (Python Preview)</DisplayName><PublisherDisplayName>ECT Local Test</PublisherDisplayName><Logo>Assets/Logo.png</Logo></Properties>
 <Dependencies><TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" /></Dependencies>
 <Resources><Resource Language="en-us" /></Resources>
 <Applications><Application Id="ECT" Executable="python\pythonw.exe" uap10:Parameters="-I -m mailarchiver.desktop_entry" EntryPoint="Windows.FullTrustApplication"><uap:VisualElements DisplayName="Email Collection Toolkit (Python Preview)" Description="Python webview reader preview" BackgroundColor="transparent" Square150x150Logo="Assets/Tile.png" Square44x44Logo="Assets/Logo.png" /></Application></Applications>
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
