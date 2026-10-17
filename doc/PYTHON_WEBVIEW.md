<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Python desktop

Python/pywebview is the supported desktop on macOS and Windows. The former
comparison branch has been integrated; its rolling experiment log is obsolete.

- [Implementation](implementation/DESKTOP.md#theory-of-operation-python-desktop-and-ingest)
  describes the shared controller, services and UI boundary.
- [Windows development](WINDOWS.md), [MSIX packaging](WINDOWS_MSIX_TEST.md) and
  [macOS distribution](MACOS_DISTRIBUTION.md) describe platform requirements.
- [End-to-end testing](END_TO_END_TESTING.md) owns acceptance layers and limits.
- [Rust GUI experiments](RUST_GUI_EXPERIMENTS.md) retains the retired migration.

Old local `.tmp/` evidence directories and earlier test counts do not validate
the current revision. Use current-head results and distinguish source, packaged,
installed and physical-interaction evidence.
