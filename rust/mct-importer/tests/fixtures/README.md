<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Public PST fixtures

These are public upstream examples, not a user's mail archive. Preserve their
bytes; tests use temporary copies for source changes and read-only permissions.
Both are Unicode PST version 23. Run with `make test-pst`.

| File | Origin | SHA-256 | License |
| --- | --- | --- | --- |
| `empty.pst` | Microsoft `outlook-pst` 1.2.0 crate, `examples/Empty.pst` | `c16ae985d12011ad510ccb3b10f19d61b0c3b3311fa4696fb56b87812bd8d5c5` | `MICROSOFT-LICENSE.txt` (MIT) |
| `mail.pst` | Aspose example `Examples/Data/MAPI/Outlook_1.pst` | `b11f87c5658a18adfbab2fe3de6ae359c6f65fc8bd6b3e45ac93d2da41ec13c3` | `ASPOSE-LICENSE.txt` (MIT) |

Microsoft source: https://github.com/microsoft/outlook-pst-rs/tree/d0f9f00110990f596ea6449c078640dc5bbf294e
(the fixture was copied from the locked crates.io 1.2.0 package).
Aspose source: https://github.com/aspose-email/Aspose.Email-for-.NET/blob/c4f369a6770490ab11bef2630b57731191354a2e/Examples/Data/MAPI/Outlook_1.pst

The empty fixture traverses two IPM folders without messages. The mail fixture
includes known example text, HTML, binary attachments, contacts, an appointment
and malformed Message-ID values. Its current Microsoft-crate baseline is 14
folders / 16 objects / 12 emitted mail / 2 non-mail / 2 extraction errors, with
30 by-value attachments. Nodes 2097316 and 2097540 fail in the upstream attachment
reader with `Missing PidTagAttachMethod on message`. Tests require a nonzero
producer exit even though all emitted records validate. This is a partial-run
fixture, not a claim that those source messages have no attachments.

Regression expectations include literal body text and `text file.txt` contents,
HTML and JPEG SHA-256 values, transport-header evidence and malformed Message-ID
retention. Decoded-output fingerprints were recorded during fixture inspection;
they are regression baselines, not independent proof of complete source recovery.
Broader Outlook-generated seed/round-trip corpora remain necessary for beta.
