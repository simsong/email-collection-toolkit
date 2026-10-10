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
New-Item -ItemType Directory -Path $cache -Force | Out-Null
$zip = Join-Path $cache 'clamav-x64.zip'
if (-not (Test-Path -LiteralPath $zip)) { Invoke-WebRequest -Uri $url -OutFile $zip }
if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $checksum) {
    throw 'ClamAV archive does not match the pinned upstream SHA-256.'
}
$reuse = Test-Path -LiteralPath $runtime
$unpacked = if ($reuse) { $runtime } else { Join-Path $cache ([guid]::NewGuid().ToString('N')) }
if (-not $reuse) { New-Item -ItemType Directory -Path $unpacked | Out-Null }
$expected = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
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
        if (-not $expected.Add($destination)) { throw 'Duplicate runtime ZIP member.' }
        if ($reuse) {
            if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) { throw "Missing cached runtime file: $relative" }
            $stream = $entry.Open()
            $hasher = [Security.Cryptography.SHA256]::Create()
            try { $digest = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '') }
            finally { $stream.Dispose(); $hasher.Dispose() }
            if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $digest) {
                throw "Cached runtime differs from verified archive: $relative"
            }
            continue
        }
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force | Out-Null
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination)
    }
} finally { $archive.Dispose() }
if ($reuse) {
    foreach ($file in Get-ChildItem -LiteralPath $runtime -File -Recurse -Force) {
        if ($file.FullName -ne $receipt -and -not $expected.Contains($file.FullName)) {
            throw "Unexpected cached runtime file: $($file.FullName)"
        }
    }
    Write-Output "Verified cached portable ClamAV against upstream archive: $runtime"
    exit 0
}
if (-not (Test-Path -LiteralPath (Join-Path $unpacked 'freshclam.exe'))) { throw 'Official archive lacks FreshClam.' }
if (-not ([IO.Path]::GetFullPath($unpacked)).StartsWith(([IO.Path]::GetFullPath($cache).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetFullPath($runtime) -ne [IO.Path]::GetFullPath((Join-Path $root '.tmp/clamav-x64'))) {
    throw 'Runtime staging paths escaped the intended task directories.'
}
Move-Item -LiteralPath $unpacked -Destination $runtime
[IO.File]::WriteAllText($receipt, $checksum + "`n")
Write-Output "Prepared branch-local portable ClamAV: $runtime"
