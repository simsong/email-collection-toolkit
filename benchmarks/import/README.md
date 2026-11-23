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
The default 50 MB corpus has 4,804 messages. About 80% of the bytes go to
one-file-per-message Maildir (the layout of a server mail spool) and 20% to MBOX.
The mix is 70% plain text, 15% text plus HTML, and 15% with base64 attachments
of 2 KB to 120 KB. The corpus also includes non-ASCII headers, threads,
duplicate Message-IDs with different content, missing Date headers and lines
that need mboxrd quoting. All addresses use `.test` domains. The corpus is
cached under `.tmp/perf-corpus/` and regenerated if the seed or size changes.

`scripts/benchmark_import.py` creates a scratch archive and runs `ect-import
--no-scan` on the corpus. It timestamps each status line to split wall time by
phase, then appends one JSON row to `results.jsonl` with the commit, machine,
per-phase seconds and messages per second. ClamAV is skipped so the numbers
measure the archive pipeline, not virus definitions.

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
