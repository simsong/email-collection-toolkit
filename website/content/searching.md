+++
title = "Searching"
description = "Find messages across your collection by words, people, dates, subjects, attachments, and original folders."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

Search covers the whole archive, across every account and year. Searching and
reading never change the archive.

![The search interface with results and a selected demonstration message](../images/search-interface.png)

*The search window with a synthetic collection. No private email is shown.*

## Words, phrases, and selectors

Type words to search message headers and text. Put a phrase in double quotes.
Every term must match. When the search box is empty, the results pane shows a
built-in reference with examples.

| Search | Finds |
| --- | --- |
| `exhibition` | The word in headers or message text |
| `"community archive"` | The exact phrase |
| `any:alex@example.org` | The address in From, To, Cc, or Bcc |
| `from:alex@example.org` | A matching sender |
| `to:sam@example.org` | A matching To recipient (also `cc:` and `bcc:`) |
| `subject:"annual report"` | A phrase in the subject |
| `date:2024-05-01` | Messages sent on that date anywhere in the world |
| `before:2024-05-01` | Messages before that date |
| `after:2024-05-01` | Messages after that date |
| `from:alex subject:exhibition after:2024-01-01` | Messages matching all three |

Search ignores case and accents and does not match word stems. Punctuation in
quotes counts: `"CACM-"` requires the hyphen. Dates can be written
`2020-01-05`, `1/5/2020`, or `"January 5, 2020"`.

A date search includes every moment that was that date somewhere on Earth, so a
message sent late in the evening in Boston still matches its local date.
Adjacent dates therefore overlap. See [Dates](@/archive-care.md#dates) for the
exact rule.

## Suggestions and filters

After three characters, the search box suggests names, addresses, subjects, and
dates, with message counts. Choose one with the arrow keys and Return, or click
it. A chosen address becomes a filter whose menu picks **Any**, **From**,
**To**, **Cc**, or **Bcc**. Click **×**, or press Delete in an empty search box,
to remove the last filter.

## Results

Sort the results by **date**, **subject**, or **sender**. Large searches show
the first 2,000 results right away and fetch the rest in the background; the
count is exact once **Searching in background** disappears.

Click a result to read it beside the list, use Up and Down to move through
results, or double-click to open the message in its own window.
**Window → New Search Window** opens another independent search of the same
archive.

## Reading a message

Search terms are highlighted in the message. Command-F finds text within the
message, and Command-G finds the next match.

The menu above the message lists its text and HTML versions plus **Raw
Source**, the complete original message. HTML is cleaned before display, and
remote images stay blocked until you choose **Load Remote Content**. Clicking
a link asks before opening it. Attachments appear below the message; opening
one always asks first.

The bottom of each message lists where it is stored in the archive and every
source where it was found. **Save Message…** saves an exact `.eml` copy.

## Attachment text

Turn on **Search attachments** to include plain-text attachments. The archive
must be indexed with attachment support first:

```sh
ect refresh-index --archive collection.mailarchive --index-attachments
```

Text from PDF, Word, Excel, and PowerPoint attachments, and text in scanned
documents and images, is not searchable yet. That work is
[planned](https://github.com/simsong/email-collection-toolkit/issues/159).

## Original folders

Turn on **Show original folder structure** to see the **Original mailboxes**
tree, built from where the mail was found. Select one or more folders to search
only those. Counts are of distinct messages, so a message found in several
backups appears once. **Show source volumes** separates folders that came from
different backup disks.

**Filter set → Save...** names a folder selection so you can return to it.
Filter sets are saved on this computer, not in the archive.

## Names and contacts

The **Names and addresses** button opens the Name matcher, which groups the
addresses that belong to one person. Drag one entry onto
another to merge them, or use **Make separate** to split an address off. Your
changes are saved in the archive right away. Double-click a name to search all
of that person's addresses. Organizations and services are hidden unless you
check **Show non-person identities**.

The command `ect human-contacts` lists your likely correspondents with dates
and message counts. See [Command line](@/command-line.md).
