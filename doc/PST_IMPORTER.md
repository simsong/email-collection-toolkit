# Microsoft-crate PST importer

`pst-importer` implements [MCT Importer API 1.0](MCT_IMPORTER_API.md) using
Microsoft's MIT-licensed [`outlook-pst` 1.2.0](https://docs.rs/outlook-pst/1.2.0/outlook_pst/).
It is a standalone extractor. Python archive-host integration, h3 duplicate
suppression and installer bundling remain separate work. The current fixture
results demonstrate useful extraction and real reader failures; this is not
complete PST beta qualification.

## Build and run

Rust/Cargo are required to build the helper. Packaged users will need the native
executable, not Rust or Outlook. The locked dependency uses no JVM or C PST library.

```sh
make rust-programs        # all three tools
make pst-importer        # only target/release/pst-importer (.exe on Windows)
make test-pst            # actual PST and process regression tests
make pst-import PST='/path/archive.pst' > recovered.mboxrd
make pst-smoke PST='/path/archive.pst'  # pipe into the discard-only validator
```

`pst-import` sends build chatter to stderr, leaving stdout exclusively for mail.
The command itself accepts `pst-importer [--] FILENAME`, plus `--help`,
`--version` (including the Microsoft crate version), and `--api-version`.
Use a byte-preserving shell pipeline and observe the producer's exit status.
Exit 0 means traversal completed within the stated scope, 1 means incomplete
extraction or I/O failure, and 2 means invalid invocation. Make also returns
nonzero when the producer fails; its numerical exit code differs from the helper's.
No real canonical archive is opened or modified by this program.

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

## Reconstructed MIME and preservation limits

PST exposes MAPI properties, not necessarily original complete RFC bytes. Output
is deterministic for a fixed source path, source bytes and tool version. H2
protects reconstructed RFC bytes including annotations; it is not original
wire-message fixity. H3 already covers selected headers and the encoded body;
no h4 is introduced. Independent exporters may produce different h3 values.

* Preserve decoded Unicode text as UTF-8; retain String8/binary body bytes with
  a known charset (UTF-8, Windows-1252, ASCII or ISO-8859-1). Unknown non-ASCII
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

Native Linux/macOS/Windows CI builds/tests this helper through the existing Cargo
workspace matrix. Local validation is on macOS; installer behavior, ANSI inputs,
RTF-only mail, embedded attachments, Exchange address resolution, encrypted mail
semantics, large files and damaged-store recovery require additional qualification.
