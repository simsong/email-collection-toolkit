<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Data-quality audit tooling

The scripts in `scripts/data_quality/` reproduce the read-only investigation
that led to the date-resolution, Babyl, Eudora/MBCP, and `From XXX` handling in
the archiver. They never modify a source mailbox or canonical archive.

`analyze_archive.py` reads an existing archive catalog and verified canonical
MBOX locations, exports every normal message dated before 1984, selects a
deterministic 100-message sample without a parsed sender, and inventories an
early source tree. `audit_babyl.py` exercises the Babyl reader across a source
tree. `summarize_evidence.py` prints compact aggregates from the generated CSV
files.

Invocation and parameters are in [CLI_REFERENCE.md](CLI_REFERENCE.md#research-and-diagnostic-commands).

The Makefile's default is also an ignored checkout-local directory. Override it
with `AUDIT_OUTPUT=/private/path` when needed. The output includes complete RFC 5322
messages, addresses, subjects, source paths, hashes, and catalog identifiers.
It is private evidence: keep it outside Git, protect it like the archive, and
delete it when the investigation is complete. Each exporter refuses to replace
an existing evidence file.

The dated findings are in [history/2026-08-27-data-quality-audit.md](history/2026-08-27-data-quality-audit.md).
