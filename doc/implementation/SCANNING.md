<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Scanning implementation

Current embedded scanner integration and configuration. Release/license
acceptance remains a separate gate; see [EMBEDDED_CLAMAV.md](../EMBEDDED_CLAMAV.md).

## Embedded antivirus migration status

[PR #124](../EMBEDDED_CLAMAV.md) uses libclamav directly from Python and Rust.
The Python session shares one compiled engine across concurrent native threads
in a temporary worker process. Source workers scan before the publication lock,
and Python records results in the existing processing database. Rust adds
X-ClamAV-Detection, X-ClamAV-Engine-Version, and X-ClamAV-Definitions-Version
only to infected messages. Python reads those headers to quarantine the emitted
RFC message without rescanning. Clean API messages have no scan provenance.
Per-message hashing and deduplication belong to Python; there are no scan receipts
or additional database schema. Operators handle failures by re-importing.

About displays definition dates and an amber recommendation after three calendar
months, with explicit FreshClam refresh into immutable per-user generations.
Failed updates cannot replace active definitions. Project-owned Python, Rust,
tools, documentation, and website code use GPL-2.0-only, with additional licenses
available. The independent converter code uses GPLv3; upstream pypff/libpff
retains LGPLv3-or-later without relicensing. The host is GPLv2-only, whose linking
compatibility differs from GPLv2-or-later; see the
[licensing explanation](../../THIRD_PARTY_NOTICES.md#gplv2-and-lgplv3-compatibility).
ClamAV uses GPLv2;
the remaining Apache-2.0 ftfy dependency needs compatibility resolution
(see THIRD_PARTY_NOTICES.md).
`make freshclam` updates ignored etc/clamdb, seeding it from
an installed database if available. The DMG bundles this copy, the engine and
FreshClam; release CI refreshes the definitions first. Its mounted self-test runs
real clean/EICAR scans using the bundled definitions. Application releases refresh
bundled definitions at least quarterly.
Before signature/dependency checks and the mounted self-test, the DMG validator
writes `dist/<DMG stem>.contents.json` with relative paths, sizes, and symlink
targets for every mounted file/link. The release job uploads this inventory with
its packaging reports even if validation fails. The release workflow passes
`--log-dmg-contents` to the builder, which prints every entry after mounting
and before executing the installed app. It separately checks for the
bundled `libclamav.dylib`; if present but unloadable, the scanner reports the
native loader's underlying error (PyInstaller's generic wrapper hides it).


## Antivirus and text extraction

`IngestRequest.scan_policy` is `clamav` by default. Explicit `not-scanned`
requests skip scanner construction and persist an `antivirus` metadata defect
in the existing catalog transaction for each new message. Run status includes
the policy (older files default to `unknown`), without changing the catalog
schema or canonical bytes. Failed required scanning still stops import.
The native source picker and Ingests page show a missing-configuration banner;
the final native confirmation defaults to Cancel and gates the opt-out.
Import confirmations use a 560-point-wide selectable AppKit accessory label,
keeping archive/source/owner paths readable without changing default or Cancel
actions. `make test-packaging` checks real alert layout and both button sets
without showing a modal dialog.
The download action opens the application's release page. About reports configuration
presence separately from readiness, which remains an ingest preflight check.
`make test-packaging` installs the packaging dependency group and exercises
the PyInstaller ctypes loader regression, missing-scanner failure, explicit opt-out,
durable evidence, source immutability, and isolated headless diagnostics.


### Embedded scanner configuration

Python `libclamav.Engine` and Rust `mct-importer::antivirus::Scanner` load official
databases with `CL_DB_PHISHING | CL_DB_BYTECODE | CL_DB_OFFICIAL_ONLY`, omitting
`CL_DB_PHISHING_URLS`. This disables the URL-phishing module and its suspicious-link
diagnostics/verdicts while retaining signature-based detections and bytecode.
Parser options and encrypted/over-limit checks are unchanged. Both file and
bounded in-memory Python scans use this engine policy; producer scan headers
remain authoritative. Already-running engines retain their prior configuration
until restarted, and existing quarantine records are not reclassified.
`make test-clamav` and `make test-clamav-rust` exercise mismatched-link HTML mail,
with and without a base64 EICAR attachment, using the real engine and definitions.

MAILARCHIVER_CLAMAV_LIBRARY and MAILARCHIVER_CLAMAV_DATABASE select explicit
development resources. MAILARCHIVER_CLAMAV_CERTIFICATES selects signature trust
material, MAILARCHIVER_FRESHCLAM the updater, and MAILARCHIVER_CLAMAV_UPDATES a
private update root. Frozen resource resolution uses the bundle rather than
Homebrew. The DMG and MSIX builders bundle the engine and definitions, and
release CI runs `make freshclam` first; see EMBEDDED_CLAMAV.md. Legacy
MAILARCHIVER_CLAMD settings are ignored.


Current MIME traversal uses the standard-library parser without explicit size,
recursion, time, or decompression limits. Plain text and rendered HTML are
indexed first for normal mail; optional extraction covers decoded text
attachments only. Future binary attachment adapters need explicit bounds and
must record failures without affecting preservation. Quarantine categories are
omitted from FTS entirely.
