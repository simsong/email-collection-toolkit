# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the installed ECT commands against disposable, authored email.
# Subprocesses use console scripts from the active uv environment, not Make.
# Creation must refuse existing content and preserve saved owner preferences.
# Both dispatcher and shortcut imports must retain source and archived bytes.
# Searching, message display and portable verification test the resulting archive.
# Invalid source combinations must fail before creating or altering a destination.
# No test accesses a personal archive, real mail account or native credential store.
"""CLI requirements: installable commands share safe archive and search behavior."""

from __future__ import annotations

from importlib.metadata import distribution
from pathlib import Path
import os
import subprocess
import sysconfig

import pytest

from mailarchiver.archive_config import load_archive_config
from mailarchiver.mailsearch import read_message_bytes


def run(command: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Execute the installed console script, retaining real stdout and error behavior."""
    executable = Path(sysconfig.get_path("scripts")) / (command + (".exe" if os.name == "nt" else ""))
    return subprocess.run([str(executable), *arguments], capture_output=True, text=True, check=False, timeout=60)


def test_installed_commands_create_import_search_and_verify(tmp_path: Path) -> None:
    """ACQUISITION/ARCHIVE: every public shortcut uses real byte-preserving archive operations."""
    scripts = {entry.name for entry in distribution("mailarchiver").entry_points if entry.group == "console_scripts"}
    assert scripts == {"ect", "ect-create", "ect-import", "ect-search"}
    archive = tmp_path / "collection.mailarchive"
    source = tmp_path / "source"
    source.mkdir()
    raw = (b"From: owner@example.org\nTo: other@example.org\nDate: Fri, 01 May 2026 10:00:00 +0000\n"
           b"Message-ID: <fixture@example.org>\nSubject: Riverbank exhibition\n\nFrom byte preservation\n")
    message = source / "message.eml"
    message.write_bytes(raw)
    created = run("ect-create", str(archive), "--owner", "owner@example.org")
    assert created.returncode == 0, created.stderr
    owner = load_archive_config(archive).owner
    assert owner is not None and owner.include == ["owner@example.org"]
    refused = run("ect", "create", str(archive))
    assert refused.returncode == 1 and "already exists" in refused.stderr
    for command, prefix in (("ect-import", ()), ("ect", ("import",))):
        imported = run(command, *prefix, "--archive", str(archive), "--no-scan", str(source))
        assert imported.returncode == 0, imported.stderr
        assert message.read_bytes() == raw
    for command, prefix in (("ect-search", ()), ("ect", ("search",))):
        result = run(command, *prefix, "--archive", str(archive), "exhibition")
        assert result.returncode == 0 and result.stdout.count("Riverbank exhibition") == 1, result.stderr
    assert read_message_bytes(archive, 1) == raw
    displayed = run("ect-search", "--archive", str(archive), "1")
    assert displayed.returncode == 0 and "From byte preservation" in displayed.stdout
    verified = run("ect", "verify", str(archive))
    assert verified.returncode == 0, verified.stderr


@pytest.mark.parametrize("arguments", [(), ("--imap", "source"), ("--source", "missing", "source")])
def test_invalid_import_does_not_create_an_archive(tmp_path: Path, arguments: tuple[str, ...]) -> None:
    """ACQUISITION: ambiguous or incomplete commands must fail before touching a destination."""
    archive = tmp_path / "absent.mailarchive"
    result = run("ect-import", "--archive", str(archive), *arguments)
    assert result.returncode == 2, result.stderr
    assert not archive.exists()


def test_create_preserves_unrelated_existing_files(tmp_path: Path) -> None:
    """ARCHIVE: creation never overwrites a nonempty destination."""
    sentinel = tmp_path / "keep.txt"
    sentinel.write_bytes(b"keep these bytes")
    result = run("ect-create", str(tmp_path))
    assert result.returncode == 1 and "not empty" in result.stderr
    assert sentinel.read_bytes() == b"keep these bytes"
