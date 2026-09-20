<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Microsoft-crate PST importer

`pst-importer` implements [MCT Importer API 1.0](MCT_IMPORTER_API.md) using
Microsoft's MIT-licensed [`outlook-pst` 1.2.0](https://docs.rs/outlook-pst/1.2.0/outlook_pst/).
The Python CLI invokes it through the PST file adapter and common processor
pipelines. h3 duplicate suppression and native installer bundling remain separate work. The current fixture
results demonstrate useful extraction and real reader failures; this is not
complete PST beta qualification.

## Build and run

Rust/Cargo are required to build the helper. Packaged users will need the native
executable, not Rust or Outlook. The locked dependency uses no JVM or C PST library.

```sh
make rust-programs        # all three tools
make pst-importer        # only target/release/pst-importer (.exe on Windows)
make test-pst            # actual PST and process regression tests
make pst-import PST='/path/archive.pst'  # emit mail directly to stdout
make pst-import PST='/path/archive.pst' > recovered.mboxrd
make pst-smoke PST='/path/archive.pst'  # pipe into the discard-only validator
```

`pst-import` sends build chatter to stderr, leaving stdout exclusively for mail.
Supply `PST` as shown (or export it in the environment). Both run targets reject
an omitted/empty value or a path that is not a readable file before building.
For a test using the checked-in public fixture:

```sh
make pst-import PST='rust/mct-importer/tests/fixtures/mail.pst'
```

This fixture emits 12 messages to stdout but intentionally returns nonzero for
two known attachment-reader failures; that partial result is expected. Use
`rust/mct-importer/tests/fixtures/empty.pst` for a successful zero-message test.

The command itself accepts `pst-importer [--only-invalid] [--] FILENAME`, plus `--help`,
`--version` (including the Microsoft crate version), and `--api-version`.
Use a byte-preserving shell pipeline and observe the producer's exit status.
Exit 0 means traversal completed within the stated scope, 1 means incomplete
extraction or I/O failure, and 2 means invalid invocation. Make also returns
nonzero when the producer fails; its numerical exit code differs from the helper's.
No real canonical archive is opened or modified by this program.

To inspect failed messages without exporting successful mail:

```sh
make pst-import PST='/path/archive.pst' ARGS=--only-invalid > invalid.mboxrd
```

This emits **diagnostic records, not recovered mail**. Each has synthetic
From/Date/Subject headers, `X-PST-Diagnostic: invalid-message`, source item URI,
the failure reason, sender/subject/code-page details, and available original
transport-header, sender, subject, plain/HTML/RTF body properties as attachments.
Unicode buffers use UTF-16LE; String8/binary buffers retain their bytes without
guessing an encoding. Original attachments and unmapped properties remain in
the source PST. Items the library cannot open have stderr diagnostics only.
If reconstruction completed its header block, `reconstructed-headers.txt`
contains those exact headers, including rejected values. These are generated
headers, distinct from any original `transport-headers` property and from the
synthetic diagnostic envelope.
The `invalid-exported` count is separate from normal `emitted` mail, and detected
extraction failures still return nonzero. Never ingest these diagnostic wrappers
as if they were successfully recovered messages.

Meeting requests and responses (`IPM.Schedule.Meeting` and subclasses) are
silently counted as non-mail and excluded. They do not cause warnings or errors.
Underlying PST reader failures are labeled `LIBRARY ERROR (outlook-pst 1.2.0)`.

## Source safety and traversal

Open the source with `File::open` and the crate's `read_from`, which supplies no
write handle. Do not use its convenience `open_store`/`open` functions: those
attempt to acquire a writer. Resolve the source to an absolute file URI, escaping
spaces, Unicode, percent signs and fragment delimiters. The generated headers are:

```text
X-Imported-URI: file:///path/archive.pst#item=2097220
X-Importer-Name: pst-importer
X-Importer-Version: 1.0.0
```

The item selector is the PST node ID, not an extraction ordinal. Stderr records
the adapter/parser/API versions, source SHA-256, reconstruction notice, failures
with node IDs, and final folder/encountered/emitted/non-mail/error counts.
Hash the source in chunks before and after extraction; also re-open the pathname
at completion to detect replacement with different bytes. A change prevents
success. This is a before/after fixity check, not a filesystem snapshot or a
proof that a concurrently modified file never changed and reverted.

Read normal contents of the entire IPM subtree, including the subtree root and
Deleted Items. Traverse child folders with cycle detection and a depth limit of
64. Check folder content counts against table rows. Search folders, associated
configuration objects, orphan/deleted-record carving and non-IPM roots are outside
this traversal. Known contact, distribution-list, appointment, task, journal and
sticky-note classes are counted as non-mail; other unsupported classes fail
visibly. Inaccessible subtree sizes remain unknown.

Recognized file variants are ANSI PST versions 14/15 and Unicode PST version 23.
Only Unicode fixtures are currently qualified. OST, version 36 and other formats
are rejected explicitly; a `.pst` suffix is not sufficient to accept a source.

## Relationship between PST and OST

PST and OST share the same underlying storage-format family and much of their
structure. Their usual roles differ: PST is a standalone store/export, while OST
is a synchronized mailbox cache. They are not distinguished solely by extension:
the header's client magic is `SM` for PST and `SO` for OST, and some OST variants
use 4-KiB pages and DEFLATE compression. See Microsoft's
[PST header specification](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-pst/c9876f5a-664b-46a3-9887-ba63f113abf5)
and libpff's
[PST/OST format documentation](https://github.com/libyal/libpff/blob/main/documentation/Personal%20Folder%20File%20%28PFF%29%20format.asciidoc).

The Rust adapter and pinned `outlook-pst` crate require PST client magic. The
Python host routes OST to the separate `pff-converter` executable using
`libpff-python==20231205`. Renaming a file does not change its internal format:
`SO` selects libpff, while `SM` remains PST even with an `.ost` suffix.

`make test-pff` exercises a genuine Unicode/version-23 OST from the public,
MIT-licensed Aspose examples. It contains 92 normal-folder objects: 87 mail
records and 5 excluded non-mail items. One mail record has an embedded MAPI
attachment that this Python binding cannot reconstruct. Its readable parent is
retained and flagged; diagnostics make the run incomplete. Compressed/version-36
OST is not yet fixture-qualified. Cache extraction never establishes completeness
of the corresponding server mailbox.

Libpff runs in the independent converter process and reads sources without write
handles. Build/install it with `make pff-converter`; the DMG bundles a standalone copy. It retains deterministic reconstructed MIME, folder/node provenance,
by-value and OLE attachments, decompressed RTF, and original transport-header
text. Missing transport headers use available MAPI properties, including display
recipient names that may lack SMTP addresses. Native body reads allocate before
output limits are checked. The host enforces a hard process deadline and bounds
output and diagnostics, killing and reaping an overdue child. Per-run receipts and streamed diagnostics are under
`processing-libpff/`. Unsupported embedded/external attachment methods retain a
flagged parent and produce an incomplete result; external references are not fetched.

## Redundant PST Import (testing option)

**Redundant PST Import** defaults off and is not exposed in GUI controls or
ordinary CLI help. For developer testing, add this to the archive's `config.yaml`
(merge into the existing `plugins` mapping):

```yaml
plugins:
  pst:
    redundant_import: true
  ost:
    timeout_seconds: 60
    max_message_bytes: 67108864
    max_folder_depth: 64
```

The same namespaces work in installation configuration; archive values override
them. `plugins.ost` governs libpff for OST and redundant PST passes. Then run the
usual CLI ingest, for example:

```sh
make run ARGS='--archive "/path/to/test-archive" ingest --owner-names-file owner-names.txt --no-scan "/path/to/pst-directory"'
make test-pff
```

The test command above deliberately opts out of antivirus; use `--clamav` for
normal ingestion. Both Rust and libpff run for each PST, even if either reports a
recoverable partial failure. Both outputs go through ordinary archive deduplication.
Exact reconstructions deduplicate on retry; differences in MIME or annotations
remain separate variants. This option does not enable the planned h3 suppression.
Each reader retains its own receipt, and either failure leaves the import incomplete.
Changing the option or reader settings invalidates the source checkpoint so an
unchanged PST is reconsidered. Repeating an unchanged successful run skips it.

## Reconstructed MIME and preservation limits

PST exposes MAPI properties, not necessarily original complete RFC bytes. Output
is deterministic for a fixed source path, source bytes and tool version. H2
protects reconstructed RFC bytes including annotations; it is not original
wire-message fixity. H3 already covers selected headers and the encoded body;
no h4 is introduced. Independent exporters may produce different h3 values.

* Preserve decoded Unicode text as UTF-8; retain String8/binary body bytes with
  a known charset (UTF-8, Windows-1252, Windows-1256, ASCII or ISO-8859-1). Unknown non-ASCII
  code pages and invalid Unicode fail instead of being replaced lossily.
* Emit text and HTML as MIME alternatives when both exist. Retain compressed
  RTF bytes as `body.rtf-compressed`; RTF decompression/rendering is not implemented.
* Preserve by-value attachment bytes using base64, MIME type, Content-ID when
  available, and RFC 2231 filenames. No attachment reference is fetched from a
  pathname, share or URL. Reference/OLE attachments fail explicitly. Embedded
  messages without their own attachments can be reconstructed; embedded messages
  containing attachments are currently rejected.
* Retain the complete available transport-header property as
  `original-transport-headers.txt`, separate from reconstructed MIME headers.
  Copy non-content transport fields, including inherited importer annotations.
  Reconstruct Subject, Date, From and Message-ID; use SMTP recipient-table
  addresses where corresponding transport recipient headers are absent.
* Decode Subject encoded-words and emit readable UTF-8 text (RFC 6532), including
  ordinary ASCII without unnecessary base64. Fold at whitespace; unusually long
  unbroken words use encoded-word folding to preserve their text within line limits.
  Remove the leading marker and prefix-length character according to
  [MS-PST subject metadata](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-pst/5959edb3-3fb0-4e35-a0dc-c043cd888fdd),
  preserving the complete textual prefix (`Re:`, `Fw:`, etc.) and subject.
* Before emitting mail, scan normal contents throughout the entire folder
  hierarchy, including nested Contacts and folders outside IPM. Cache all populated
  contact email slots in memory, without retaining bodies or photos. Resolve
  Email1/Email2/Email3 through PSETID_Address and the store's named-property map.
  Use explicit address/SMTP pairs from the corresponding
  [contact email properties](https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxprops/2e73fa51-c757-4ce8-8b0c-a70fbd6ee444)
  and DN aliases from validated
  [Exchange address-book EntryIDs](https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxcdata/b00b2824-8434-4294-a0e7-b4e336489ccc).
  Supplement them with sender/represented-sender properties and recipient tables
  elsewhere in the same PST. Match DNs case-insensitively; require unambiguous
  SMTP evidence and keep the source sender display name when available.
  Preserve the DN in `X-PST-Original-Sender` and identify the evidence item/property
  in `X-PST-Sender-Resolution`. Missing or conflicting mappings retain the native
  DN in `From` with `X-PST-Sender-Address-Type: EX`, which the archive validator
  accepts. No network directory lookup or address guessing is performed.
  Reconstructed To/Cc/Bcc also use this lookup, with original-recipient and
  resolution headers. Existing sender, recipient, reply, resent and return-path
  fields resolve whole native values or angle-bracket addresses; names/comments
  are not searched for substitutions. Original transport-header bytes remain
  attached. Unresolved recipients without usable SMTP evidence still fail visibly.
  Stderr reports contact, email-slot, mapping, conflict and unreadable counts.
  Unreadable lookup objects prevent success, counted once if mail extraction also
  encounters them. Search folders, associated configuration objects and orphan carving remain
  outside the contact scan.
* Missing Date uses submission/delivery FILETIME, then an explicit epoch
  placeholder; missing From uses `unknown@invalid.invalid`. These are generated
  values, not inferred historical facts. Malformed optional Message-ID values
  are retained as `original-message-id.txt` instead of an invalid RFC header.
  Original transport evidence remains available. Other metadata that cannot
  satisfy the API profile prevents that record from being emitted.

MAPI Unicode/string properties are the crate's decoded values; the crate strips
terminators and does not promise preservation of malformed original property
encodings. Unmapped MAPI properties and unsupported object types remain in the
unchanged source PST. Keep that PST as the original evidence.

Each message is spooled to a private, automatically removed temporary file,
limited to 64 MiB of serialized record output, and passed through the same API
validator before any bytes of that record reach stdout. Base64 output uses small
chunks. The upstream crate eagerly allocates property, table and attachment
buffers; the output cap is **not** a strict parser-memory limit. Host process
resource controls and large/corrupt-file qualification remain beta work.

A failed item emits no partial record; later readable items can still be emitted.
Any failure prevents a successful run. Output I/O failure may leave an incomplete
stdout tail, so consumers must honor the process status. A valid stream alone
cannot establish completeness.

## Checked evidence

`make test-pst` uses checked-in public upstream fixtures with their licenses and
hashes in [the fixture inventory](../rust/mct-importer/tests/fixtures/README.md).
Microsoft's empty Unicode PST yields two folders and zero messages successfully.
Aspose's example yields 14 folders, 16 encountered objects, 12 valid messages,
two known non-mail objects and two failed messages. The 12 messages include
30 by-value attachments. Both failed messages report the crate's missing
`PidTagAttachMethod` error; the run must return nonzero. These failures must not
be converted into success or inferred zero-length attachments.

Regression assertions cover known body text, HTML and attachment hashes,
transport-header evidence, malformed Message-ID retention, deterministic output,
Unicode/space/percent filenames, unchanged read-only source bytes, corrupt and
missing inputs, source changes, and real producer/validator/broken-pipe processes.
The fixture count is an observed regression baseline, not independent proof
that every object in an arbitrary PST can be recovered.

The single macOS CI job builds/tests this helper through the Cargo workspace and
exercises its Python CLI integration through `make check`. Installer behavior, ANSI inputs,
RTF-only mail, embedded attachments, broader Exchange/contact variants, encrypted mail
semantics, large files and damaged-store recovery require additional qualification.
