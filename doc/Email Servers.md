<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Email servers

## Current acquisition paths

The standalone Python IMAP adapter synchronizes once, read-only, on macOS and
Windows. Native OS secrets storage holds passwords; archive configuration holds
server/port/username. Generic accounts use mailbox/UIDVALIDITY/UID checkpoints;
Gmail uses All Mail, account-scoped message IDs and labels. Full MIME enters the
ordinary ingest pipeline. [CLI_REFERENCE.md](CLI_REFERENCE.md#imap-downloads) owns
setup, options, selection defaults and counter semantics.

| Source | Available path | Remaining boundary |
| --- | --- | --- |
| Generic IMAP | Password/app-password CLI over verified TLS | No OAuth, scheduler, GUI account setup or graphs |
| Gmail | IMAP All Mail when app passwords are permitted; extracted Takeout MBOX | No Gmail API/OAuth; All Mail excludes Spam/Trash-only mail |
| Microsoft 365 | Local PST/OST or complete Apple Mail cache | No OAuth IMAP or Graph adapter |
| Local exports/cache | Existing source plugins | Cache/export contents do not prove server completeness |

Provider-specific workflows are in [GMAIL.md](GMAIL.md), [M365.md](M365.md) and
[APPLE_MAIL_CACHE.md](APPLE_MAIL_CACHE.md).

## Adapter decision

Own the provider adapters so source immutability, byte preservation, provenance,
checkpointing and canonical publication share the existing framework. Acquisition
checks durable publication before advancing state. Credentials and transient
provider cursors do not replace canonical content integrity. The general source
registry, refresh/rebuild UI and provider-specific incremental history remain
plans in [requirements.md](plans/ACQUISITION.md#per-archive-sources-and-import-modes).

The earlier survey considered imapcopy, imapsync, mbsync/isync, OfflineIMAP3,
imap-backup and Got Your Back. They may be useful acquisition/comparison tools,
but none is the canonical writer. Server-to-server synchronization and mutable
mirror semantics do not meet the archive's read-only-source contract. Historical
license/capability assertions should be rechecked before adopting a tool.

Future Gmail API and Microsoft Graph adapters may add provider-native history.
Authorization and registration must be designed and verified when implemented;
neither is an existing product feature.
