<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# PST and OST ingest executables

**Status: updated 2026-09-13.** [MCT Importer API Version 1.0](MCT_IMPORTER_API.md)
defines the contract, with implemented Rust generator and validator programs.
The standalone [PST adapter](PST_IMPORTER.md) uses Microsoft's crate and the
OST adapter uses the separate libpff converter. PST email recovery is complete
for this project's collection purpose; OST extraction is explicitly best effort.
See [MBOX_READING.md](MBOX_READING.md) and [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md)
for current storage and hash controls.

## Contract

An ingest executable accepts one source filename, reads it without modification,
and writes an **mboxrd byte stream to stdout**. The host launches an argument
array, never shell interpolation. Diagnostics go to stderr; progress, banners
and JSON must never be interleaved with mail. Windows implementations must set
standard streams to binary mode. The filename is local; a source URI describes
provenance and is not an instruction for the host to fetch another resource.

Each record has a separate `From ` delimiter and RFC 5322/MIME payload. Add
these fields at the beginning of the payload, in this order, before mboxrd quoting:

```text
X-Imported-URI: file:///C:/Mail/archive.pst#item=12345
X-Importer-Name: outlook-pst-adapter
X-Importer-Version: 0.1.0
```

These are illustrative values, not a shipped command/version. Escape URI paths
correctly, including spaces and non-ASCII characters; HTTPS source URIs are
permitted. Prefer a stable source-native item selector to an exporter ordinal.
Record the complete source SHA-256 separately because paths/URLs can identify
changed bytes. The importer version identifies the adapter; run provenance must
also identify the upstream parser revision and executable SHA-256.

Exactly the first three physical header lines are the generated provenance block,
one nonempty single-line ASCII value for each name. Preserve same-named source fields later in the
original header block. Reject malformed/missing generated fields as a protocol
error, retaining diagnostic evidence. Values cannot inject additional fields or
unescaped controls. Record the executable actually launched independently of
its claimed headers. No combined `X-Importer` field or version parsing is needed.

The receiver removes one mboxrd quote level to obtain `MailObject.raw`, then
uses the existing Python scanner, observation, catalog and writer path. All
added fields participate in h2. Publication adds one mboxrd quoting level again.
Preserve existing MIME bytes when available; do not parse/reserialize simply to
insert headers. For property-based PST or OST items, the importer constructs
recovered MIME and records that construction in run provenance. This is the
collection format for recovered email; the unchanged PST or OST remains the
source record.

## Completion and recovery

Require exit code 0 for a completed traversal of the selected email scope and
nonzero for unreadable mail or incomplete extraction. Calendar, contacts,
virtual search folders, configuration, and other Outlook administrative objects
are outside that scope and are not fabricated into email. An OST is best-effort
cache extraction: a successful run says what was recovered from the cache, not
what existed in the corresponding server mailbox.

Buffer/spool at most one bounded record at a time, using private temporary
storage for large messages. Drain stderr concurrently to avoid pipe deadlock.
Do not release the final record until successful process exit: a crash may
leave a tail that looks like valid but truncated MIME. Earlier complete records
may be published with a partial-run observation. Retain failed-tail evidence
separately, record exit status, and permit reruns. EOF is not success.
Cancellation terminates/reaps the process and retains the last valid checkpoint.
Detailed diagnostics/run-manifest schemas and size limits remain implementation work.

## Readers and deduplication

The PST reader is Microsoft's [outlook-pst-rs](https://github.com/microsoft/outlook-pst-rs).
The OST reader is the separate libpff converter. They are selected by the
source's internal format, not its filename extension. The collection does not
run competing readers for an ordinary import.

Existing dedup uses normalized Message-ID plus h2. Different importer headers
change h2, so that key retains both annotated variants. H3 already includes
selected headers **and the entire encoded MIME body**, ignoring the three
added top-level fields. It remains a comparison control, not an automatic drop
rule. A shared Message-ID or source item does not prove identical recovered content.

Use the existing h3 for comparisons across importer annotations and h2 for
exact variant integrity. No h4 or additional message-content fingerprint is
needed. Preserve every source/importer observation. Enabling h3-based archive
duplicate suppression remains separate implementation work; these test tools
do not change current Message-ID-plus-h2 dedup behavior or redefine either hash.

## What PST and OST recovery produces

The authoritative Microsoft specifications distinguish:

* [PidTagTransportMessageHeaders](https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxomsg/28f67517-0f35-4b87-a78d-8d5029141db0): a copy of the original message's **header section**, ending at the first blank line; it does not supply the original MIME body.
* [MS-OXCMAIL, message bodies](https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxcmail/59290e68-5bc7-4a2c-9824-846db0f365bc): text, HTML and RTF have property representations; MIME writers generate body elements and may preserve or change character encoding.
* [PidTagMimeSkeleton](https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxcmsg/2dd82209-0184-4321-a5ba-f0a10b8f1ec8): conversion metadata can retain MIME headers and content not converted into MAPI properties. This is not a universal guarantee of an untouched complete original MIME stream.

Recovered messages can have MIME boundaries, encodings, folding, or attachment
order chosen by the importer. That is normal for a PST/OST collection workflow:
the useful result is deterministic, readable, interoperable email with retained
source provenance. The PST or OST itself is never rewritten and remains available
when its native Outlook representation matters.

## Executables and installers

The subprocess contract removes language ABI coupling to Python, but leaves
per-platform builds, signing and dependency qualification. Bundle selected
executables and dependencies; users need no compiler, Python, Java or Outlook.

| Target | Package work | Acceptance evidence |
| --- | --- | --- |
| Windows x64 initially | Build native Rust adapter; bundle required DLLs; include helper in installer/signing inventory | Native installed ingest, binary pipes, Unicode/space paths, NTFS locking, cancellation, scanning and h2 preservation; ARM64 separately qualified |
| macOS arm64; Intel separately if offered | Bundle helper inside app; audit Mach-O dependencies; sign nested code before the app; follow notarization/stapling policy | Installed/quarantined app without developer runtimes, relocation, architecture and independent archive verification |
| Linux Snap amd64 initially | Stage helper/dependencies against selected base; run inside confinement; use writable user staging | Installed strict-confined Snap, permitted home/removable-media access, cancellation, refresh and archive reopening; each architecture separately built |

Rust-only initial delivery avoids a JVM. Libpff/libpst add their native
build/dependency/license obligations; a Java importer adds a private JVM, JAR,
security updates and package size. None changes the stream contract. Strict
Snap confinement cannot assume arbitrary external executables will work:
bundled importers are the initial Snap scope. Test actual shipped helpers and
record their versions/hashes.

The macOS builder bundles the selected PST and OST import executables. Windows
delivery remains v1.1 work: its installer must bundle the equivalent helpers and
validate native newline handling, locking, cancellation, scanning, and recovery.

## Validation

Validation exercises deterministic recovered MIME, attachments, malformed
properties, source read-only behavior, process failure handling, and the
installed macOS bundle. PST runs use public upstream fixtures and may use
user-selected source copies without retaining private mail in the repository.
OST results are reported as
best effort, including explicit incompleteness where the cache or reader cannot
provide a recoverable email item.

Neither reader repairs a source or contacts a live account. Tests use synthetic
or explicitly authorized copied fixtures.
