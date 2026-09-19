<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# PST test corpus acquisition

Keep the checked-in inventories and downloader in `pst/`; keep downloaded data
in ignored `var/pst/`. The downloader is `pst/pst-downloader.rs`, a Cargo binary
package included in the root workspace. Python would be sufficient for this
orchestration, but the Rust implementation shares the importer toolchain and
needs no Python interpreter or external archive-extraction executable.

Run these commands from the project/worktree root:

```sh
make pst-download-plan                  # list everything, no network or output files
make pst-download                       # all direct PSTs, ZIPs and the listed 7z
make pst-download PST_DOWNLOAD_ARGS='--scope fixtures'
make pst-download PST_DOWNLOAD_ARGS='--scope archives'
make pst-download PST_DOWNLOAD_ARGS='--scope fixtures --limit 1'
make test-pst-downloader                 # temporary local HTTP servers, no corpus downloads
```

`make pst-downloader` builds `target/release/pst-downloader` (`.exe` on Windows);
`make rust-programs` also builds it. `--inventory-dir` and `--output` override
the defaults `pst` and `var/pst`, relative to the current directory. `--limit N`
selects the first N distinct URLs in sorted order. `--help` lists all options.
Do not put private mail or credentials into a tracked inventory.

Corpus downloads are **development-only**. CI/CD, packaging and release checks
use PST fixtures already in `rust/mct-importer/tests/fixtures/`, including
`mail.pst`; they do not need any discovered remote PST or a populated `var/pst/`.
Locked build-dependency installation is separate from corpus acquisition.

## Inventory and storage

Every `*.json` in the inventory directory is read (16 MiB per inventory limit). Supported entries are
`fixtures[]` (`download_url`, `size_bytes`, `sha256`), `enron_packages[]`
(`url`, optional `download_size_bytes`/`zip_size` and `files[]` member checks),
`enron_all_130_download_urls[]`, and `additional_documented_source.download_url`.
Other research fields are retained in the input but not interpreted as commands
or proof of successful acquisition. Empty/unrecognized inventories, conflicting
checksums/sizes and invalid URLs fail before downloading. Repeated URLs merge.

The supplied inventory plans 86 direct PST URLs (239,416,320 listed bytes),
130 Enron ZIP URLs and one unverified Dropbox 7z URL. Most archive sizes are
unknown: the reported known-byte sum is a lower bound, not a capacity estimate.
The eight detailed Enron entries enrich URLs already in the 130-link list.
The Dropbox link is attempted in an all/archive run and failures are recorded;
no login, alternate mirror discovery or account access is attempted.

```
var/pst/
  objects/<sha256>.pst                    # deduplicated byte-for-byte PSTs
  downloads/<sha256-of-url>/source.*      # retained HTTP PST/ZIP/7z artifact
  downloads/<sha256-of-url>/receipt.json  # original/final URL, observed hash and size
  reports/run-*.json                     # retained report per invocation
  download-report.json                   # latest run, updated after each URL
```

Reports map each observation to source inventory paths/SHA-256 values, URL, archive member (when
applicable), object path, byte count and SHA-256. They distinguish comparison
against a supplied SHA-256 from an observed hash only. An available object with
matching expected digest may satisfy another source URL without requesting it;
therefore a completed URL is not proof that its endpoint is reachable. Source
filenames and archive member paths are metadata, never output filesystem paths.

## Integrity, limits and reruns

The downloader requires HTTPS, except numeric loopback HTTP for local tests;
redirects obey the same rule. Credentials/fragments in URLs are rejected. It
requires a complete HTTP 200 response, validates provided lengths/digests, rejects
HTML masquerading as an archive, and checks PST `!BDN`/`SM` header signatures.
Header recognition is not proof of parser compatibility or message completeness.
It does not import, parse mail, scan messages or rewrite PST bytes.

Transfers stream chunks to temporary files; hashing and extraction use 64 KiB buffers. Hashes are computed from the
completed files during validation, before non-overwriting publication, rather
than during download or extraction. Existing artifacts/objects are hash-checked before reuse; corrupt
cache files are preserved and reported, never silently replaced. Successful
objects survive later failures. Rerun to reuse completed downloads; interrupted
transfers restart rather than using HTTP range resume. Receipts are saved before
publishing complete artifacts, so a crash between those operations is retryable.
An older artifact without a receipt is preserved under a unique `unreceipted-*`
subdirectory beside it and downloaded again. A receipt without an artifact is
also retryable; existing corrupt files with receipts remain visible failures.

ZIP and 7z PST members are streamed to content-addressed objects. Traversal names,
PST symlinks/reparse points, duplicate member names and missing expected members
fail. Encrypted/unsupported compression fails without external extraction tools.
Other members are not saved. Limits default to 16 GiB per HTTP artifact and
64 GiB expanded bytes per archive, including non-PST members; override with
`--max-download-bytes` and `--max-expanded-bytes`. Requests have a 30-second
connection timeout and a configurable 1,800-second total timeout. Decoder/directory
allocations are not capped by the output-byte limit. Limits are per source, not a
total disk budget; retained compressed artifacts and PST objects both consume space.

Failures are reported and later URLs are attempted. Exit 0 means every selected
job succeeded; exit 1 means a download/extraction/validation failure; invalid CLI
arguments use exit 2. Ctrl+C stops pending HTTP waits, checks for cancellation
between buffered hashing/extraction operations, removes active temporary files
and `.download.lock`, and exits 130. Cancellation during a native decoder call
waits for that call to return. Completed downloads and reports remain available;
run the same `make pst-download` command to verify/reuse them and retry unfinished
or failed URLs. This resumes the job list, not partial HTTP byte ranges.

The cache uses nonblocking exclusive [OS file locks](https://doc.rust-lang.org/std/fs/struct.File.html#method.try_lock)
(requires Rust 1.89+). `.download.lock` contains a diagnostic PID and is itself
locked; existence or PID does not establish ownership. On startup an abandoned
file, including the old existence-only lock, is taken over automatically if its
OS lock can be acquired. Process death releases OS locks, including when forced
termination prevents file cleanup. A permanent `.download.guard` file is also
locked throughout the run to prevent a race between deleting/recreating
`.download.lock` and another process opening it. Its presence is harmless;
neither file should be manually removed while a downloader runs. These advisory
locks coordinate cooperating downloaders; they do not protect against manual
cache edits. Upgrade after stopping any older downloader, which did not acquire
OS locks.

Run reports preserve earlier invocation results; the latest report
only describes its selected scope. Acquisition does not establish redistribution
rights in embedded messages or attachments; the inventory retains source notices.
