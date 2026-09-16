+++
title = "Plugin system"
description = "Existing source and file plugins, and the proposed ranked mailbox, message, and content processing graphs."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

Production import currently uses source and file-parser plugins. A headless
API v2 framework with executable test plugins is now available through the CLI;
production wiring remains planned. The
diagram below describes the **proposed** expansion into three processing trees;
it shows the complete target architecture, beyond the current framework stage.

[![Proposed container, message, and content processing DAGs, with ranked scanning, MIME dispatch, transactional publication, and resumable handoffs](../images/processor-dag.svg)](../images/processor-dag.svg)

Open the graphic for a larger view. The file-parser layer is a subtree of local
container acquisition, which supplies the three pipelines below. Planned
geography and visualization extension points are not implemented plugin trees.

## Processing and handoffs

Source discovery and file-format plugins recover original messages before the
three processing pipelines:

1. **Ingest:** ClamAV scans the raw message at rank 1. Infected messages go through
   the write service to INFECTED and stop. At rank 2, the filing/handoff plugin
   deduplicates, publishes the original bytes, and durably queues message processing.
2. **Message processing:** header extraction stores basic metadata in SQLite.
   A handoff plugin queues content processing; other per-message subscribers
   can register independently.
3. **Content processing:** the MIME extractor emits typed body and attachment
   objects for subscribers. Identity extraction is the first evidence consumer;
   authoritative person matching remains later work.

The framework completes each rank before applying higher-ranked subscribers
to the same type. Required cross-type outputs precede their consumers.
A plugin may stop the current part, stop the message, or fail the import.
Every plugin has access to all headers and all content of its current message.
Types and body/attachment settings control invocation, not access. The filing
plugin may read whatever it needs after ClamAV; full metadata storage stays
in the second pipeline.

The proposed TOML settings declare rank, body/attachment/both scope, and scanner
timeout (60 seconds by default). End-of-run statistics report each plugin's
invocation count, total time, shortest, longest, and average invocation.
About will list registered plugins by subscribed type.

## Incomplete work

When an archive opens with unfinished work, show **Incomplete work** with two
initially checked choices: **Continue ingest** and **Continue content
processing**. Confirmed work resumes from durable checkpoints. Unchecking both
opens the archive without starting either job. The dialog returns on the next
opening while work remains. When both are selected, ingest finishes before deferred content processing.
Selecting only content processing handles eligible already-ingested messages,
even while other ingest work remains unfinished. Required message-metadata
jobs precede their content jobs.

## Synthetic content and attached messages

Synthetic or virtual objects inherit their source's body/attachment scope and
provenance. If the message body has no plain-text alternative, prefer HTML over
RTF when creating synthetic text/plain content. A text attachment does not count
as a plain-text body. Synthetic representations are processing inputs, not new
canonical mail or MIME parts added to the original message.

Attached emails are proposed as first-class child messages with a path back to
their parent message and MIME part, visible in the message viewer. They start
independent jobs at message processing, bypassing ingest and another antivirus
scan. The child retains its parent's scan provenance. The handoff uses shared
deduplication/publication services to establish the child record and content
reference. This creates a new job rather than a cycle within one job's DAG.
Their own bodies and attachments have local scopes; the child message retains
its attachment-origin provenance. Depth and expansion limits must prevent
unbounded recursion while preserving the parent bytes.

Standard Message-ID and raw SHA-256 deduplication shares one canonical message
and extracted metadata across standalone and attached copies. Each occurrence
retains its own provenance, including the parent and MIME-part path. An
attachment tag means the message was also observed as an attachment.

Child messages receive an attachment tag whose initial background is 5% gray.
The planned [tag editor](https://github.com/simsong/email-collection-toolkit/issues/119)
will store tag IDs, names, nullable background/text colors, crosshatching, and
font choices in SQLite, with configurable tagged-message display.

See the [full plugin contract](https://github.com/simsong/email-collection-toolkit/blob/main/doc/PLUGINS.md)
for the implemented API and the proposed extension.

## Writing a processor

The [processor contract](https://github.com/simsong/email-collection-toolkit/blob/main/doc/PLUGINS.md#registration-and-manifest-contract)
specifies the proposed API v2 manifest, one typed processing object and result,
framework content types, rank barriers, aborts, timeout enforcement, archive
services and durable handoffs. Existing source/file API v1 plugins remain
supported through adapters; production adapters are the next integration stage.

Jobs checkpoint plugin versions, configuration and input identity so retries
can skip completed work. Publication and queue handoff share a recovery
protocol. Scanner failure never counts as clean; child messages retain parent
scan provenance. Manual identity edits and tags survive processing reruns.
