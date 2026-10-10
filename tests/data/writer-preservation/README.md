# Writer preservation fixtures

These four synthetic messages are the exact samples requested for the portable
writer and GUI-import acceptance tests. Read them with `Path.read_bytes()`;
`.gitattributes` disables Git text conversion for these EML files.

- `2024/duplicate-crlf.eml`: CRLF headers/body, unquoted and quoted `From ` body
  lines, no terminal newline; shares its Message-ID with `2024/duplicate-lf.eml`.
- `2024/duplicate-lf.eml`: LF headers/body and terminal newline, different content
  under the same Message-ID. Both messages must survive SHA-256 deduplication.
- `2024/invalid-utf8.eml`: declares UTF-8 but contains the bytes FF FE 80.
  Derived decoding may recover or replace characters; storage/export must not.
- `2024/broken-mime.eml`: declares a multipart boundary that never occurs and has
  no terminal newline. Preserve it and offer Raw Source when no body is parsed.

All four omit `Date` and live in the `2024/` subdirectory, so direct corpus
ingestion and focused tests both require the documented `path-year` fallback. They are not antivirus fixtures.

Exact fixture SHA-256 hashes:

- `278458367da2ea5903baf3a69edfe63c13c180aec40a7a1115d2c28230f1f8d1` — `2024/duplicate-crlf.eml`
- `ae7a5214d575546f032a2040f6ff126ae4331d433282d3f385d79fb819299b0e` — `2024/duplicate-lf.eml`
- `5a83c7b99916a82e7788af94ff0f9a27a4597c955cce0a60d301ca263a7d0523` — `2024/invalid-utf8.eml`
- `f91682d2e0486fa12f36a688cd8cc31e9c7167e67862bc12dc952ba9e910279a` — `2024/broken-mime.eml`
