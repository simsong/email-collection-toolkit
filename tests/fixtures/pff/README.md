<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Genuine OST fixture

`sample.ost` is Aspose.Email-for-.NET's `Examples/Data/MAPI/SampleOstFile.ost`,
from commit `c4f369a6770490ab11bef2630b57731191354a2e`:
https://github.com/aspose-email/Aspose.Email-for-.NET/blob/c4f369a6770490ab11bef2630b57731191354a2e/Examples/Data/MAPI/SampleOstFile.ost

Unmodified upstream example, MIT license in `ASPOSE-LICENSE.txt`.
SHA-256: `5ff8bc133935a03fad8241f271a604e1cc41e36640d6538d6bcc9bd39e6d9866`.
Size: 4,080,640 bytes. Header client magic `SO`, Unicode version 23.

The libpff test enumerates 92 objects outside search folders: 80 mail records
and 11 non-mail records. It checks incomplete-item diagnostics for one embedded
MAPI attachment, stable reconstruction, source fixity,
HTML/RTF and attachments, plus CLI ingestion/search. This fixture does not
qualify compressed/version-36 OST or prove server-mailbox completeness.

This partial-import fixture is separate from `tests/data`, whose golden corpus
requires complete imports. `make test-pff` checks both retained mail and failures.
Independent native-property reads provide these decoded-content SHA-256 values:

* Node 2182532, attachment 0 (JPEG, 20,833 bytes):
  `0f4ff85802be13ad86f82b7e6e886e5a9a6a77ddb84995bc5a7657a3d40c8f9a`.
* Node 2182532, HTML body:
  `6d52c74376341391bbe58f07ea0fc880578a7fb4160c31eff6b6a2c19017da91`.
* Node 2182916, decompressed RTF body:
  `e2822fc116bc97613129eec5833d5728a347683452a4951c1ca3c8b4ddd39355`.
