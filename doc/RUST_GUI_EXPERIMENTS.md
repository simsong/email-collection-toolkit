<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Experimental Rust UI archive

The experimental Rust UI was retired on **October 10, 2026** at
[`4c4b85f`](https://github.com/simsong/email-collection-toolkit/commit/4c4b85f70e88a27df3ab7e78f29294b320b62f37),
“Restore Python desktop and preserve retired Rust GUI.” Its source, private
Python helper, packaging adapters and dedicated tests are removed from the active
checkout. Routine code analysis and supported builds exclude this experiment.

- Signed tag: [`experimental-rust-ui`](https://github.com/simsong/email-collection-toolkit/tree/experimental-rust-ui).
- Preservation branch: [`codex/experimental-rust-ui`](https://github.com/simsong/email-collection-toolkit/tree/codex/experimental-rust-ui).
- The branch's signed `[skip ci]` marker has the same file tree as the tag; it
  avoids running obsolete release jobs on archival publication.
- The [detailed retrospective before removal](https://github.com/simsong/email-collection-toolkit/blob/41d3f71bcd63e92e5254900f8846d3cb0c88dca2/doc/RUST_GUI_EXPERIMENTS.md)
  remains in Git history. Reconstruct historical UI work only when explicitly requested.

Python/pywebview is the supported desktop. Independent Rust importer, verifier,
PST, MIME decoder and build tools remain supported workspace members.
