# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Define the connection budget independently of message-processing workers.
# Each configured IMAP account receives its own concurrent acquisition budget.
# Eight connections leave room below Gmail's fifteen-connection account limit.
# Preferences and launch contexts share strict whole-number validation.
# Active imports retain their budget until the next start or continuation.
# No credentials or archive state belong to this application preference.
"""Shared bounds for the per-account IMAP connection preference."""
from typing import Annotated

from pydantic import Field, TypeAdapter

DEFAULT_IMAP_CONNECTIONS = 8
MAX_IMAP_CONNECTIONS = 15
ImapConnectionLimit = Annotated[int, Field(ge=1, le=MAX_IMAP_CONNECTIONS, strict=True)]


def validate_imap_connections(value: int) -> int:
    return TypeAdapter(ImapConnectionLimit).validate_python(value)
