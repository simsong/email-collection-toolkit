# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Encode and decode exactly one level of reversible MBOX payload quoting.
# How it works:
# Add/remove a greater-than sign only on From-like payload lines, separately from the envelope.
# Fundamental dependents:
# - mbox: canonical storage must round-trip arbitrary original quote depth.
# - sources: declared mboxrd inputs need the inverse decoding rule.

"""Reversible MBOXRD storage quoting; inputs exclude the record separator."""

import re

_FROM_LINE = re.compile(br"(?m)^(?=>*From )")
_QUOTED_FROM_LINE = re.compile(br"(?m)^>(?=>*From )")


def quote(raw: bytes) -> bytes:
    """Add one greater-than sign to every From-like payload line."""
    return _FROM_LINE.sub(b">", raw)


def unquote(stored: bytes) -> bytes:
    """Remove exactly one storage quote, retaining original quote depth."""
    return _QUOTED_FROM_LINE.sub(b"", stored)
