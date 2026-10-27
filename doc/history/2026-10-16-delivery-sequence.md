<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# 2026-10-16-Delivery-Sequence

**Historical record.** Archived October 16, 2026. Statements, counts and findings
describe the recorded revisions, not current support. Use the
[documentation guide](../README.md) for current behavior.

Snapshot: combined IMAP/documentation work on `codex/imap-cli`, based on
`b9ae56bb58075df451ade72fbf9e9dc81fd9faf7`. The sequence below predates the split
and mixes work already implemented with unimplemented extensions. It is kept as
history; the [future-work index](../plans/README.md) owns deferred scope.

## Delivery sequence

1. Create the package, configuration model, versioned fresh schema, MBOX/EMLX
   readers/writer, and `verify` command.
2. Implement recursive local ingest, exact dedupe, autosave exclusion,
   integrity files, sorting, recovery, and ClamAV routing.
3. Add `review`, `refresh-index`, FTS5 rebuilding, and conservative body/HTML extraction.
4. Add the generic IMAP importer with resumable provider checkpoints, Gmail
   and Microsoft OAuth profiles, and archive-configured Refresh/Rebuild.
5. Add provider-specific Gmail API and Microsoft Graph importers when their
   richer metadata justifies the separate transports.
6. Build the local search/view interface on the stable database and MBOX
   retrieval API.
