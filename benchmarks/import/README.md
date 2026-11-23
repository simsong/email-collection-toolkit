<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Import performance benchmark

This benchmark tracks import speed across commits. It uses only synthetic mail.

```console
make benchmark-import                      # 50 MB corpus, appends a result row
make benchmark-import BENCH_MB=5           # quick check
make benchmark-import BENCH_LABEL=my-change BENCH_WORKERS=4
```

`scripts/perf_corpus.py` builds the corpus from a fixed seed. The same seed and
size always give byte-identical mail, and `MANIFEST.json` records its SHA-256.
About 80% of the bytes go to one-file-per-message Maildir (the layout of a
server mail spool) and 20% to MBOX. The mix is 70% plain text, 15% text plus
HTML, and 15% with base64 attachments of 2 KB to 120 KB. The corpus also
includes non-ASCII headers, threads, duplicate Message-IDs with different
content, missing Date headers and lines that need mboxrd quoting. Every Subject
ends with a unique token (`subjectz000123z`), and every body with another
(`bodyz000123z`). All addresses use `.test` domains. The corpus is cached under
`.tmp/perf-corpus/` and regenerated if the seed or size changes.

Eighteen messages also carry real documents from `attachments/`: two each of
`.txt`, `.pdf`, `.docx`, `.doc`, `.xls`, `.xlsx`, `.ppt`, `.pptx` and `.jpeg`
(the JPEG is a picture of text). Each holds a unique identifier such as
`documentz03zpdfz1`, built from letters and digits only, so that full-text
search indexes it as one word. `scripts/make_benchmark_attachments.py`
regenerates these files with LibreOffice. They are committed, so the corpus
needs no LibreOffice to build. Corpus version 2 (with these tokens and
documents) is not comparable with version 1 rows, so compare rows only within
one `corpus_version`.

`scripts/benchmark_import.py` creates a scratch archive and runs `ect-import
--no-scan --index-attachments` on the corpus. It timestamps each status line to
split wall time by phase, then appends one JSON row to `results.jsonl` with the
commit, machine, per-phase seconds and messages per second. ClamAV is skipped
so the numbers measure the archive pipeline, not virus definitions.

After timing stops, `scripts/corpus_verify.py` checks the archive end to end.
It regenerates the corpus and its expected metadata from the seed, then fails
the run unless all of the following hold:
- The catalog holds exactly the corpus's messages, with unique Subjects.
- Each message's catalog location pointer into the archived MBOX, and
  `read_message_bytes`, return exactly the expected bytes, and their SHA-256
  matches. Maildir files are stored verbatim. The `.mbox` records keep their
  `>From` quoting, because an undeclared MBOX dialect is never unquoted.
- Subject, sender, recipients, date (or, for undated mail, its non-Date
  source), Message-ID and attachment bytes all match what was generated.
- Search lists each message's attachments, and each Subject and body token
  finds exactly its own message. 50 sampled body words find their messages.
- Each document identifier finds its message for every type an installed
  processor reads. Today that is only `.txt`. The other types are recorded
  as `verification_gaps` and turn into hard failures automatically when an
  extractor for that type lands (#159, #163, #19).
- No processing job is unfinished, and the standalone archive verifier passes.

The row records `verified`, `verification_errors` and `verification_gaps`, and
the command exits non-zero if verification fails.
`tests/test_import_crash_recovery.py` runs the same check after crash recovery,
and it checks that deliberate damage (a wrong Subject, sender, pointer or hash,
or a missing search row) is detected.

Only compare rows from the same machine. To count fsyncs per message, which is
the main cost on slow or networked disks, run the importer under
`strace -f -c -e trace=fsync,fdatasync` on Linux.

## Recorded results

The first rows of `results.jsonl` come from a 4-CPU Linux cloud container
(4,804 messages):

| Commit | Change | Wall time | Ingest | Body/attachment processing | Messages/s |
| --- | --- | --- | --- | --- | --- |
| 056648a | baseline | 920 s | 281 s | 617 s | 5.2 |
| this PR | grouped commits, throttled progress | 318 s | 152 s | 154 s | 15.1 |
| dcfc5bb | + single-write objects, config cache | 247 s | 112 s | 122 s | 19.4 |
| be32340 | + batched processing commits, warm working copies, one directory sync per commit | 210 s | 128 s | 69 s | 22.9 |
