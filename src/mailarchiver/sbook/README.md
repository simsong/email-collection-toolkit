<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# SBook5 name-classification data

Unmodified upstream sources from `simsong/SBook5` commit
`d88ee99496e546ec75228b87298a6b1e19de0ff4`:

- `libsbook/parse_company.fp`: company codewords and explicit person exceptions.
- `libsbook/company_names.txt`: additional company expressions.
- `libsbook/parse_address.fp`: organization words (`P_ORG`).
- `libsbook/first_names.txt`: given-name gazetteer.

Copyright Simson Garfinkel; the upstream MIT permission notice is in
`LICENSE.txt`. Preserve these sources and that notice when updating.

`name_entities.py` uses whole-word rules of the form `{S}pattern/{END}` and the
company-name list, with literal Flex quote translation and Unicode boundaries.
It deliberately does not emulate scanner ordering, address/city/country/stock
classification, general suffix/prefix rules or the full SBook sorter. A recognized
given name (including surname-first comma spellings) conflicting with a company
word stays unknown and visible. No match
is unknown, rather than proof that a name describes a person. This is a
conservative adaptation of the knowledge base, not a claim of SBook equivalence.

Source: https://github.com/simsong/SBook5/tree/d88ee99496e546ec75228b87298a6b1e19de0ff4/libsbook
