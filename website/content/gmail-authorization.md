+++
title = "Authorize Gmail"
description = "Connect your Gmail account without creating a Google Cloud project."
+++

Gmail ingest is not implemented yet. This page describes the authorization
experience that a completed Mail Archiver release will provide.

## What you do

Replace the example with the Gmail or Google Workspace address you want to
archive:

```console
uv run mailarchiver-auth your.name@gmail.com
```

Mail Archiver usually recognizes Google Workspace domains automatically. If it
cannot identify yours, use:

```console
uv run mailarchiver-auth --gmail person@example.org
```

The command opens Google in your system browser. Sign in with the same account
you entered on the command line and approve read-only Gmail access. That is the
entire end-user setup: **you do not create a Google Cloud project, register Mail
Archiver, or obtain a client ID.**

Mail Archiver verifies the account Google returns and stores your refresh token
in your operating-system credential store. It does not put the token in your
archive. Authorization grants no permission to delete, label, move, or mark
messages as read.

## What “seven days” means

The Mail Archiver project and Desktop client are registered once by the release
maintainer. They do not expire after seven days and do not need to be recreated.

If the shared Google project is in **Testing**, Google requires the maintainer to
list each test account. Each test user's authorization expires after seven days,
so that user must approve access again. The project and program registration
remain unchanged.

If the project is **In production**, users do not have to be added individually.
An unverified personal-use app can serve fewer than 100 users, but Google shows
an unverified-app warning and applies a lifetime cap of 100 new users. See
[Google's Audience documentation](https://support.google.com/cloud/answer/15549945)
and [personal-use exception](https://support.google.com/cloud/answer/13464323).

## If Mail Archiver asks you to register an app

It should not. A message saying that no distributed Gmail client is present
means the package is an incomplete development build. Report it to whoever
built or distributed that copy of Mail Archiver.

The illustrated [one-time registration reference](../oauth-client-registration/)
is for release maintainers, not Gmail users.
