# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Prepare the official portable x64 ClamAV dependency within this branch only.
# Verify the archive against the digest published with the upstream release.
# Preserve upstream licenses, certificates, and dependent native libraries.
# Do not install a service, modify PATH, or alter machine configuration.
# Definition acquisition and real scanner acceptance are separate Make targets.
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$checksum = '0d9e0228b2674137ea1a2853566c98a0278ad52ab2582c3d6dbd75373848c395'
$url = 'https://github.com/Cisco-Talos/clamav/releases/download/clamav-1.5.4/clamav-1.5.4.win.x64.zip'
$cache = Join-Path $root '.tmp/clamav-download'
$runtime = Join-Path $root '.tmp/clamav-x64'
$receipt = Join-Path $runtime 'upstream-sha256.txt'
if (Test-Path -LiteralPath $runtime) {
    if ((Test-Path -LiteralPath $receipt) -and (Get-Content -LiteralPath $receipt -Raw).Trim() -eq $checksum) {
        Write-Output "Portable ClamAV already prepared: $runtime"
        exit 0
    }
    throw 'Existing ClamAV directory has no matching provenance; preserve it and inspect manually.'
}
New-Item -ItemType Directory -Path $cache -Force | Out-Null
$zip = Join-Path $cache 'clamav-x64.zip'
if (-not (Test-Path -LiteralPath $zip)) { Invoke-WebRequest -Uri $url -OutFile $zip }
if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $checksum) {
    throw 'ClamAV archive does not match the pinned upstream SHA-256.'
}
$unpacked = Join-Path $cache ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $unpacked | Out-Null
$archive = [IO.Compression.ZipFile]::OpenRead($zip)
try {
    $libraries = @($archive.Entries | Where-Object { $_.Name -eq 'libclamav.dll' })
    if ($libraries.Count -ne 1) { throw 'Official archive must contain exactly one native scanner.' }
    $prefix = $libraries[0].FullName.Substring(0, $libraries[0].FullName.Length - 'libclamav.dll'.Length)
    $boundary = [IO.Path]::GetFullPath($unpacked).TrimEnd('\') + '\'
    foreach ($entry in $archive.Entries) {
        if (-not $entry.Name -or -not $entry.FullName.StartsWith($prefix, [StringComparison]::Ordinal)) { continue }
        # Build symbols and static SDK libraries consume hundreds of megabytes and
        # are not runtime dependencies. Keep all DLLs, programs, certificates/licenses.
        if ([IO.Path]::GetExtension($entry.Name) -in @('.pdb', '.lib', '.exp')) { continue }
        $relative = $entry.FullName.Substring($prefix.Length)
        $destination = [IO.Path]::GetFullPath((Join-Path $unpacked $relative))
        if (-not $destination.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Upstream ZIP member escapes the task runtime directory.'
        }
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force | Out-Null
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination)
    }
} finally { $archive.Dispose() }
if (-not (Test-Path -LiteralPath (Join-Path $unpacked 'freshclam.exe'))) { throw 'Official archive lacks FreshClam.' }
if (-not ([IO.Path]::GetFullPath($unpacked)).StartsWith(([IO.Path]::GetFullPath($cache).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetFullPath($runtime) -ne [IO.Path]::GetFullPath((Join-Path $root '.tmp/clamav-x64'))) {
    throw 'Runtime staging paths escaped the intended task directories.'
}
Move-Item -LiteralPath $unpacked -Destination $runtime
[IO.File]::WriteAllText($receipt, $checksum + "`n")
Write-Output "Prepared branch-local portable ClamAV: $runtime"
