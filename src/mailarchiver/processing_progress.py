# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Read queue accounting for the import status driver without changing processing.
# Each canonical email is counted once across its unfinished jobs and MIME parts.
# Only archived messages qualify; future downloads and unpublished inputs do not.
# Pending, running and failed work includes jobs blocked by unfinished parents.
# The caller owns the existing SQLite connection and controls refresh frequency.
# The status driver uses this read-only query; no queue or archive state is changed.
# Keeping reporting outside the processor package preserves its replay fingerprint.
"""Distinct saved-message backlog accounting for import progress."""
from __future__ import annotations

import sqlite3


def saved_messages_remaining(database: sqlite3.Connection) -> int:
    """Count archived emails with unfinished work, including retryable failures."""
    return database.execute(
        "SELECT count(DISTINCT state.catalog_message_pk) FROM jobs "
        "JOIN message_state AS state ON state.message_id=json_extract(jobs.item_json,'$.message_id') "
        "WHERE jobs.status IN ('pending','running','failed') AND state.catalog_message_pk IS NOT NULL"
    ).fetchone()[0]
