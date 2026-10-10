# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Resolve installed Windows SDK tools on hosted or local Windows machines.
# Use the native process architecture without pinning an image's SDK version.
# Require both packaging and signing tools from the same SDK directory.
# Fail early when the SDK is missing rather than silently downloading tools.
# Callers may explicitly select a different SDK where their interface permits it.
function Get-WindowsSdkTools {
    $arch = if ([Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture -eq 'Arm64') { 'arm64' } else { 'x64' }
    $base = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/bin'
    foreach ($version in (Get-ChildItem -LiteralPath $base -Directory | Where-Object Name -Match '^10\.0\.\d+\.0$' | Sort-Object { [version]$_.Name } -Descending)) {
        $tools = Join-Path $version.FullName $arch
        if ((Test-Path (Join-Path $tools 'makeappx.exe')) -and (Test-Path (Join-Path $tools 'signtool.exe'))) { return $tools }
    }
    throw 'Install a Windows SDK containing MakeAppx and SignTool.'
}
