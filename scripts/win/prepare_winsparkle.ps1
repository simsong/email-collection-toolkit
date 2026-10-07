# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Stage the official WinSparkle runtime beside a native Windows reader build.
# The SDK version and SHA-256 pin an external dependency, not the app release.
# Downloaded SDK bytes are verified before extraction or DLL loading.
# The requested architecture selects one SDK DLL and its redistribution notices.
# Outputs remain in the requested build directory; no system installation occurs.
# CI and local ABI validation share this same staging operation.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('ARM64', 'x64')]
    [string]$Architecture,
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$sdkVersion = '0.9.4'
$sdkSha256 = '6037df37fc263bd1650a1c4949681a9d40ffe991d01f35892a406cb5d103c976'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$sdkCache = Join-Path $projectRoot ".tools/winsparkle/$sdkVersion"
New-Item -ItemType Directory -Force -Path $sdkCache | Out-Null
$sdkArchive = Join-Path $sdkCache "WinSparkle-$sdkVersion.zip"
if (-not (Test-Path -LiteralPath $sdkArchive)) {
    Invoke-WebRequest -Uri "https://github.com/vslavik/winsparkle/releases/download/v$sdkVersion/WinSparkle-$sdkVersion.zip" -OutFile $sdkArchive
}
if ((Get-FileHash -LiteralPath $sdkArchive -Algorithm SHA256).Hash -ne $sdkSha256) {
    throw 'WinSparkle SDK SHA-256 mismatch'
}
Expand-Archive -LiteralPath $sdkArchive -DestinationPath $sdkCache -Force
$sdkRoot = Join-Path $sdkCache "WinSparkle-$sdkVersion"
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
Copy-Item -LiteralPath (Join-Path $sdkRoot "$Architecture/Release/WinSparkle.dll") -Destination (Join-Path $OutputDirectory 'WinSparkle.dll')
Copy-Item -LiteralPath (Join-Path $sdkRoot 'COPYING') -Destination (Join-Path $OutputDirectory 'WinSparkle-COPYING.txt')
Copy-Item -LiteralPath (Join-Path $sdkRoot 'COPYING.expat') -Destination (Join-Path $OutputDirectory 'WinSparkle-COPYING.expat.txt')
Write-Output "Staged checksum-verified WinSparkle $sdkVersion ($Architecture) in $OutputDirectory"
