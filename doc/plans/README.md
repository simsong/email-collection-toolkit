<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Future requirements and designs

These documents describe unimplemented extensions. They are not release notes,
commands to run today or evidence of shipped support. A partial implementation
is identified explicitly; current contracts remain in the
[requirements overview](../requirements.md) and source behavior in the
[implementation overview](../implementation.md).

| Plan | Future scope | Current boundary |
| --- | --- | --- |
| [Acquisition](ACQUISITION.md) | General source registry; Refresh/Rebuild; provider OAuth/APIs; Eudora and general offline caches | Password-based IMAP and supported local formats already work |
| [Archive extensions](ARCHIVE_EXTENSIONS.md) | Automatic catalog migrations; MBOX sorting; integrated verification; PDF bag integration; redaction/research derivatives | Current catalog versions are validated; search can be rebuilt; portable and independent Rust verifiers exist |
| [Contacts and geography](CONTACTS.md) | Contacts window; geography data/refresh; located-versus-affiliated evidence; archive snapshots | Contacts CLI, identity editing and synthetic matcher exist; no authoritative matcher selected |
| [Printed-email PDF/OCR](PDF_OCR.md) | General OCR, segmentation and document-derived archive integration | Standalone Poppler text extraction creates a reviewable derivative |

IMAP scheduling, graphical counters, background agents and desktop account setup
remain future work. The current standalone CLI runs once and exits; its options
and hourly accounting are in the [CLI reference](../CLI_REFERENCE.md#imap-downloads).

Use this index to distinguish planned work from gaps in platform acceptance.
An implemented feature that lacks native/release evidence remains in its current
subsystem document with that limitation; it is not relabeled as future design.
No milestone or date is promised here. Product priorities live in
[project direction](../project_direction.md), while earlier release-scope decisions
are preserved only in [history](../history/README.md).
