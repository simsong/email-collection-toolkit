<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Windows desktop development

The supported Windows desktop is Python/pywebview with WinForms and WebView2.
The MSIX bundles private x64 CPython, application resources, ClamAV and WinSparkle.
Windows ARM machines may run it under x64 emulation; native ARM64 is not a package
target. WebView2 is external. Independent Rust tools remain supported; the Rust
GUI is retired.

## Prerequisites

Use native Windows for acceptance of filesystem locking, dialogs, Explorer and
packaging. WSL cannot establish those behaviors. Source development needs Git,
uv, Python 3.12+ and GNU Make with a working shell. Use x64 Python to match the
package and native dependencies. Rust/MSVC and a Windows SDK are needed when
building independent Rust tools; Dioxus is not required.

Synchronize locked dependencies before running the desktop. See the
[CLI reference](CLI_REFERENCE.md#desktop) for launch syntax and
[MSIX packaging](WINDOWS_MSIX_TEST.md) for packaging entry points.

## Current behavior and acceptance

The shared Python engine supports archive creation/import, processing, identity
edits, verified reading, search, exports and native writer locking. Open uses
the standard system dialog; choosing a file within a collection resolves its
archive. Read-only SQLite handles preserve locking for mapped/UNC paths.
Ctrl shortcuts complement macOS Command shortcuts. Native file drag-out and
packaged PST/OST helpers remain unavailable on Windows.

Source tests, relocated-package tests, installed activation/upgrade/uninstall,
physical interaction and updater-driven replacement are separate gates. Old
Rust or unsigned Python artifacts do not establish current-head acceptance.
See [testing](END_TO_END_TESTING.md), [antivirus](EMBEDDED_CLAMAV.md),
[updates](SPARKLE_UPDATES.md) and [public signing](WINDOWS_PUBLIC_SIGNING.md).
