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
import sqlite3
import subprocess
import sys
import sysconfig

import pytest

from mailarchiver.archive_config import load_archive_config
from mailarchiver.application import create_empty_archive
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.mailsearch import read_message_bytes


def run(command: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Execute the installed console script, retaining real stdout and error behavior."""
    executable = Path(sysconfig.get_path("scripts")) / (command + (".exe" if os.name == "nt" else ""))
    return subprocess.run([str(executable), *arguments], capture_output=True, text=True, check=False, timeout=60)


@pytest.mark.parametrize("entry", ["imap", "import", "shortcut", "engine", "imap-module"])
def test_configuration_failure_prints_repair_message(tmp_path: Path, entry: str) -> None:
    """ACQUISITION: public CLI settings failures print one actionable diagnostic and leave settings untouched."""
    archive = tmp_path / "collection.mailarchive"
    create_empty_archive(archive)
    config = archive / "config.yaml"
    content = "imap_sources: PRIVATE_CONFIGURATION_VALUE\n"
    config.write_text(content)
    arguments = ["--archive", str(archive)]
    if entry == "imap":
        result = run("ect", "imap", *arguments, "--check-credentials")
    elif entry == "import":
        result = run("ect", "import", *arguments, "--imap", "--no-scan")
    elif entry == "shortcut":
        result = run("ect-import", *arguments, "--imap", "--no-scan")
    else:
        source = tmp_path / "message.eml"
        source.write_bytes(b"Subject: synthetic\n\nfixture\n")
        module = "mailarchiver" if entry == "engine" else "mailarchiver.imap_cli"
        options = ["ingest", "--no-scan", str(source)] if entry == "engine" else ["--check-credentials"]
        result = subprocess.run([sys.executable, "-m", module, *arguments, *options],
                                capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 1
    assert "imap_sources: expected a list" in result.stderr and "Repair config.yaml" in result.stderr
    for private in ("Traceback", "ValidationError", "input_value", "PRIVATE_CONFIGURATION_VALUE"):
        assert private.casefold() not in result.stderr.casefold()
    assert config.read_text() == content


@pytest.mark.parametrize("scope", ["installation", "archive"])
@pytest.mark.parametrize("content", [
    "plugins: PRIVATE_INSTALLATION_VALUE\n",
    "plugins:\n  identity-evidence:\n    provider_domains: PRIVATE_INSTALLATION_VALUE\n",
    "plugins:\n  identity-evidence:\n    signature_lines: PRIVATE_INSTALLATION_VALUE\n",
    "plugins:\n  text-index:\n    max_text_bytes: PRIVATE_INSTALLATION_VALUE\n",
    "plugins:\n  mime:\n    max_parts: PRIVATE_INSTALLATION_VALUE\n",
    "plugins:\n  attached-message:\n    max_depth: PRIVATE_INSTALLATION_VALUE\n",
])
def test_installation_configuration_failure_stays_concise_in_console_and_history(tmp_path: Path, scope: str, content: str) -> None:
    """ACQUISITION: invalid settings stop before processing and omit inputs/frames in console and saved runs."""
    archive = tmp_path / "collection.mailarchive"
    create_empty_archive(archive, owner_rules=OwnerRules(include=["owner@example.test"]))
    installation = tmp_path / "installation.yaml"
    installation.write_text("{}\n")
    settings = installation if scope == "installation" else archive / "config.yaml"
    settings_text = content + ("owner:\n  include: [owner@example.test]\n" if scope == "archive" else "")
    settings.write_text(settings_text)
    source = tmp_path / "message.eml"
    raw = b"From: owner@example.test\nDate: Fri, 1 May 2026 10:00:00 +0000\nSubject: synthetic\n\nfixture\n"
    source.write_bytes(raw)
    result = run("ect", "import", "--archive", str(archive), "--no-scan", "--installation-config", str(installation), str(source))
    assert result.returncode == 1
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        row = database.execute("SELECT detail FROM ingest_runs ORDER BY run_pk DESC LIMIT 1").fetchone()
    if row is None:
        assert scope == "archive" and content == "plugins: PRIVATE_INSTALLATION_VALUE\n"
    for diagnostic in (result.stderr, *((row[0],) if row is not None else ())):
        assert f"invalid {scope} configuration" in diagnostic and f"Repair {settings.name}" in diagnostic
        for private in ("private_installation_value", "traceback", "validationerror", "input_value"):
            assert private not in diagnostic.casefold()
    assert settings.read_text() == settings_text and source.read_bytes() == raw
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM messages").fetchone() == (0,)


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
