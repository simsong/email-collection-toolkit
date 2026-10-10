# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Diagnose opening an explicitly selected existing archive without ingestion.
# Validate databases read-only; do not trigger hot-journal recovery here.
# Exercise both mapped-drive and canonical network paths through real queries.
# Check member resolution for BagIt and SQLite selections in the same archive.
# Fetch one message through the shared search and verified reader services.
# Print only pass/fail stage information, never private message contents.
"""Read-only archive-opening acceptance through Make."""
import argparse
from pathlib import Path

from mailarchiver.application import archive_from_selection, validate_archive
from mailarchiver.gui_service import search_page, describe_message
from mailarchiver.mailsearch import read_message_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    root = args.archive.absolute()
    canonical = root
    for selected in (root, root / "bagit.txt", root / "archive.sqlite3", root / "search.sqlite3"):
        resolved = archive_from_selection(selected)
        if resolved != root:
            raise AssertionError("Archive member did not resolve to the selected collection")
        _, canonical, _ = validate_archive(resolved)
        print(f"Validated {selected.name}", flush=True)
    for archive in (root, canonical):
        page = search_page(archive, "", limit=1)
        if page.error or not page.results:
            raise AssertionError(f"Archive search failed: {page.error}")
        message_pk = page.results[0].message_pk
        if not read_message_bytes(archive, message_pk):
            raise AssertionError("Verified message read returned no bytes")
        describe_message(archive, message_pk)
        print("Search and verified message rendering passed", flush=True)
    print("Archive opening passed; no ingestion or database recovery performed.")


if __name__ == "__main__":
    main()
