<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Standalone PST/OST converter

This independent GPLv3 program uses libpff-python to read PST/OST files without
modification and writes MCT Importer API mboxrd to stdout. It does not import
mailarchiver, load libclamav, hash canonical messages, or access an archive database.
The archive application consumes this standard output and owns deduplication.

GPLv3 covers our converter code. Upstream pypff/libpff retains its
LGPL-3.0-or-later license; this design does not require relicensing it. See the
[GPLv2/LGPLv3 compatibility explanation](../../THIRD_PARTY_NOTICES.md#gplv2-and-lgplv3-compatibility).

From the repository root, run `make pff-converter` to install its separately
locked environment. Invoke `converters/pff/.venv/bin/pff-converter -- SOURCE.ost`
(Windows: `converters/pff/.venv/Scripts/pff-converter.exe`).
`make pff-converter-bundle` produces the standalone PyInstaller directory under
`target/pff-converter/` for bundling. `make test-pff` exercises both external
readers, source preservation, repeated imports, limits and real antivirus scans.

Optional `--receipt` and `--diagnostics` files retain source/version/count evidence.
`--offset N` and `--limit N` select a stable ascending-node-ID range of
normal-content items before mail-class filtering. The converter uses the same
Date/From/Subject/Message-ID normalization and UTC mboxrd delimiter policy as
the Rust PST importer.
Source SHA-256 is recorded; no per-message hashes are computed.
Exit 0 means complete extraction; exit 3 means all emitted records are complete
but some source items were incomplete. Other failures must not be treated as
complete output. The host enforces a hard process deadline and bounded evidence.
Operators handle failures by re-importing through normal deduplication.

For scanned imports the application runs the separate GPLv2 `mcti-scan` tool on
the output before API admission. That executable uses libclamav and adds detection,
engine-version and definition-version headers only to infected messages.
