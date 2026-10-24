<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Maintained Rust tools

The supported workspace contains the independent archive verifier, MCT importer
tools, PST tools, MIME transfer decoder and build runner. Use `make rust-programs`
to build and `make rust-check` to validate them. See the
[CLI reference](../doc/CLI_REFERENCE.md#rust-tools) and
[MCT contract](../doc/MCT_IMPORTER_API.md).

The experimental desktop UI and its helper/tests have been removed from this
checkout. The signed `experimental-rust-ui` tag and `codex/experimental-rust-ui`
branch preserve its retirement snapshot; see the [archive note](../doc/RUST_GUI_EXPERIMENTS.md).
