<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Maintained Rust tools

The supported workspace contains the independent archive verifier, MCT importer
tools, PST tools, MIME transfer decoder and build runner. Use `make rust-programs`
to build and `make rust-check` to validate them. See the
[CLI reference](../doc/CLI_REFERENCE.md#rust-tools) and
[MCT contract](../doc/MCT_IMPORTER_API.md).

The desktop GUI migration is retired and excluded from the supported workspace.
Its architecture, measurements, build history and acceptance evidence are
consolidated in [Rust GUI experiments](../doc/RUST_GUI_EXPERIMENTS.md).
