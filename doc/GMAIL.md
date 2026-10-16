<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Gmail acquisition

## IMAP All Mail

The standalone IMAP CLI supports password/app-password authentication when the
account permits it. Follow [CLI_REFERENCE.md](CLI_REFERENCE.md#imap-downloads)
for setup and options. It downloads complete MIME only from special-use All
Mail, using `X-GM-MSGID` for identity and `X-GM-LABELS` for mailbox membership.
Spam/Trash-only mail is outside that mode. It does not visit label folders for
additional body/header downloads. There is no Google OAuth client in the project.

Google controls app-password availability. Check its current
[app-password instructions](https://support.google.com/accounts/answer/185833)
for the account rather than assuming ordinary passwords work.

## Google Takeout

Takeout is an offline snapshot path that does not authorize this program to
access Google. In [Google Takeout](https://takeout.google.com/), deselect other
products, select Mail and the desired mailboxes, then create a one-time export.
Download every part, retain the original archives and extract them under one
source directory. Use the [local ingest command](CLI_REFERENCE.md#ingest) on
that directory and verify the result before discarding any acquisition copy.
Direct Takeout ZIP import, automatic export creation and automatic download are
not implemented. Follow Google's current export/download limits rather than
relying on a fixed expiration or part-size value in this document.

Takeout's `X-Gmail-Labels` headers are preserved as source bytes; they are not
yet structured search fields. They are distinct from the IMAP `X-GM-LABELS`
metadata retained by the live CLI. Overlapping exports deduplicate exact message
content while preserving source observations. Different raw variants remain.

See Google's [data export guidance](https://support.google.com/accounts/answer/3024190)
and [Gmail export guidance](https://support.google.com/mail/answer/10016932).

## Apple Mail cache

Complete `.emlx` records provide a local bridge when Gmail is already synchronized
with Apple Mail. Partial records and detached attachments prevent a completeness
claim; see [APPLE_MAIL_CACHE.md](APPLE_MAIL_CACHE.md).

## Deferred authorization work

Gmail API, OAuth IMAP and provider history integration remain unimplemented. A
future distributed client needs a maintainer-owned public-client registration,
local private token storage and a fresh review of Google's scope/verification
requirements. End users should not be asked to register their own application.
[OAUTH_CLIENT_REGISTRATION.md](OAUTH_CLIENT_REGISTRATION.md) records the absence
of current registration; it is not an onboarding procedure.
