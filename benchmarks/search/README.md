<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Search latency benchmark

This benchmark times desktop word search end to end on an existing archive. It
opens the archive read-only and records only timings and counts.

```console
make benchmark-search BENCH_ARCHIVE=~/mail-archive.mailarchive
make benchmark-search BENCH_ARCHIVE=... BENCH_QUERIES="gandalf information"
make benchmark-search BENCH_ARCHIVE=... BENCH_VOCABULARY=empty
```

`scripts/benchmark_search.py` starts a fresh Python process per query, like a
newly launched app, and calls the same `search_page` and `search_count` the app
uses: first page, count, then both again warm. `BENCH_VOCABULARY=empty` uses an
empty vocabulary cache to time the first search of an index never searched
before. The target fails when a first page takes 1 s or more, or a first page
plus count takes 5 s or more. Only compare rows from the same machine.

## Recorded results

Apple silicon Mac, 1,199,572-message archive with a 7.8 GB legacy search index.
Seconds; "first" is the first search in a new process.

| Change | Query (hits) | First page | First count | Warm page | Warm count |
| --- | --- | --- | --- | --- | --- |
| baseline c538fff | gandalf (137) | 44.3 | 3.5 | 3.0 | 3.1 |
| baseline c538fff | harvard | 26.2 | 0.5 | 4.2 | 0.4 |
| this change | gandalf (137) | 0.71 | 0.47 | 0.56 | 0.46 |
| this change | harvard | 0.35 | 0.07 | 0.21 | 0.07 |
| this change | information (265,000) | 1.02 | 0.22 | 0.89 | 0.21 |
| this change | e11 (236) | 0.93 | 0.65 | 0.74 | 0.57 |
| this change, empty vocabulary cache | gandalf | 11.3 | 0.47 | 0.56 | 0.46 |

With an empty cache the first search pays the one-time vocabulary scan of a
changed index (10–30 s). The desktop app starts that scan in the background
when a window opens an archive, and stores it for later launches.
