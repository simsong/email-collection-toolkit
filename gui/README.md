<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Desktop interface

This directory contains the HTML, CSS and JavaScript used by the Python/pywebview
desktop on macOS and Windows. Native hosts use WKWebView and WebView2. The shared
application controller owns archive documents, search windows and import jobs;
canonical writes use the ingest engine and OS writer lease.

For user workflows, see the [user manual](../doc/USER_MANUAL.md). The
[desktop implementation](../doc/implementation/DESKTOP.md) owns controller,
bridge, lifecycle and platform details.

## Run from a checkout

```sh
uv sync --locked
uv run --locked ect gui --archive collection.mailarchive
```

Omit `--archive` for recent/open/new startup, or use `--new` for new setup.
`make gui` is the developer convenience launcher; on macOS it supplies the signed
native development identity and then invokes the same ECT command implementation.
The installed desktop application bundles its own runtime and assets.

## Search and import

The interface supports multiple archives and independent search windows, source
selection, saved owner rules, import progress, suspension and recovery. Search
and message views use verified archived bytes. Remote message content is blocked
by default. The application serves its assets through a nonce-authenticated local
server and exposes only explicitly selected bridge methods.

Imports populate the search index. Rebuild a saved archive's disposable index with:

```sh
uv run --locked ect refresh-index --archive collection.mailarchive --index-attachments
```

`--index-attachments` includes supported text attachments; PDF/Office text
extraction remains planned. Preserve the whole archive when backing up: manual
identity and tag decisions cannot be rebuilt from MBOX alone.

## Validate changes

Use `make test-gui` and the headless browser suite in `make test-e2e`. Native
menus, dialogs and platform integrations have separate acceptance targets in
the [testing guide](../doc/END_TO_END_TESTING.md); headless success alone does not
establish native platform correctness. `make gui-smoke` checks the real macOS
JavaScript bridge.

Regenerate screenshots with `make website-screenshots` after interface changes,
using only the synthetic collection, and inspect the relevant website pages.
