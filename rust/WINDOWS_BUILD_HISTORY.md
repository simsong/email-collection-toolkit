<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Historical Windows Rust setup

Retired; see [README](README.md).

The retired Windows GUI used Rust, Wry/Tao, WebView2, and the shared web
interface. The commands below document its historical **Cargo from PowerShell**
workflow. For the supported Python desktop, use [Windows setup](../doc/WINDOWS.md).

## Prerequisites

- Rust 1.99 or newer with an MSVC host toolchain: `aarch64-pc-windows-msvc`
  for native Windows ARM64 or `x86_64-pc-windows-msvc` for x64.
- Visual Studio C++ build tools for that architecture and a Windows SDK.
- Microsoft Edge WebView2 Runtime for the graphical application.
- A checkout containing the Cargo aliases in `.cargo/config.toml`.

The checkout's `rust-toolchain.toml` selects 1.99.0 with rustfmt and Clippy.
Check the selected toolchain with `cargo --version` and `rustc -vV`.
If Cargo is not on PATH in the current PowerShell session, use:

```powershell
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
```

## Compile and launch a synthetic archive

Run these commands from the checkout root. The demo command creates a new
synthetic archive and refuses an existing destination; run it only once per
destination, or choose a new directory name. It does not import personal mail.

```powershell
cargo reader-build
New-Item -ItemType Directory -Force .tmp | Out-Null
cargo reader-demo ".tmp/windows-gui-demo"
cargo run-ect --archive ".tmp/windows-gui-demo"
```

The `run-ect` command builds as needed and launches the WebView2 GUI. Search for
`observatory` or `café` to try the fixture. To launch the compiled executable
directly, without invoking Cargo:

```powershell
.\target\debug\mailsearch-webview.exe --archive ".tmp/windows-gui-demo"
```

For the existing fixture in this VM's working branch:

```powershell
Set-Location "C:\Users\simso\gits\mail-archiver\.tmp\windows-rust-gui"
cargo run-ect --archive ".tmp/windows gui café"
```

Pass another existing archive directory to `--archive` to read it. The reader
opens archives read-only; it does not create, import, repair, or migrate them.

## Validate and build an optimized executable

```powershell
cargo reader-check
cargo reader-native-check
cargo reader-smoke ".tmp/windows-gui-demo" observatory
cargo reader-probe ".tmp/windows-gui-demo" observatory
cargo reader-build --release
.\target\release\mailsearch-webview.exe --archive ".tmp/windows-gui-demo"
```

`reader-check` runs formatting, Clippy, and Rust reader tests in order. Smoke
and probe commands exercise the archive engine without opening a window;
they do not prove native menu, keyboard, or WebView2 rendering behavior.
Executable paths above assume the default Cargo target directory.

Native ARM64 compilation, WebView2 launch/navigation/IPC, and synthetic
archive tests have run on this VM. Full interactive acceptance, Windows
imports, and installer packaging remain incomplete. Development builds can
run without WinSparkle configuration; update checks then report unavailable.
See [the architecture and acceptance handoff](../doc/WINDOWS_RUST_HANDOFF.md) for
the remaining porting work.

## Batch import

The retired Windows Rust GUI read existing archives and did not batch import.
At that time the Python importer also refused Windows archive writing. The
restored Python desktop now provides the shared Windows import path described
in [Windows setup](../doc/WINDOWS.md). Cargo's historical helper commands were
not a replacement for the canonical archive writer.

For the currently implemented macOS batch workflow, see
[Batch import from an external drive](../README.md#batch-import-from-an-external-drive).
That section explains recursive discovery, skipped-file notices, and failures
when directories cannot be read.
