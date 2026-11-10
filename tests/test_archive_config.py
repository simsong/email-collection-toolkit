# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify actionable diagnostics for expected archive and installation settings problems.
# Real YAML fixtures exercise schema, syntax and file-access failure boundaries.
# Messages identify settings and repair actions without dumping input values.
# Missing and empty configurations retain their supported default behavior.
# Parsing remains read-only, including when supplied configuration is invalid.
# GUI and CLI integration checks separately exercise presentation of these results.
"""ACQUISITION/DESKTOP: configuration problems are concise, private and recoverable."""
from pathlib import Path

import pytest

from mailarchiver.archive_config import ArchiveConfig, load_archive_config, save_archive_config
from mailarchiver.configuration_diagnostics import ConfigurationError
from mailarchiver.plugin_configuration import load_installation_config, read_plugin_configuration
from mailarchiver.ingest_diagnostics import format_failure


@pytest.mark.parametrize("content,reason", [
    ("imap_sources: PRIVATE_CONFIGURATION_VALUE\n", "imap_sources: expected a list"),
    ("imap_sources: [PRIVATE_CONFIGURATION_VALUE\n", "YAML syntax"),
    ("owner:\n  include: [PRIVATE_CONFIGURATION_VALUE@]\n", "Incomplete email pattern"),
    ('"bad\\noption": true\n', "bad option"),
    ("[]\n", "expected a mapping"), ("false\n", "expected a mapping"), ("0\n", "expected a mapping"),
])
def test_configuration_problem_has_actionable_private_message(tmp_path: Path, content: str, reason: str) -> None:
    """Invalid settings report fields/actions without validator traces or configuration values."""
    path = tmp_path / "config.yaml"
    path.write_text(content)
    with pytest.raises(ConfigurationError) as failure:
        load_archive_config(tmp_path)
    message = str(failure.value)
    assert str(path) in message and reason in message and "Repair config.yaml" in message
    assert "private_configuration_value" not in message.casefold()
    assert "ValidationError" not in message and "input_value" not in message
    assert all(character.isprintable() for character in message)
    assert format_failure(failure.value) == message and "Traceback" not in message
    assert failure.value.__cause__ is None and failure.value.__suppress_context__
    assert path.read_text() == content


def test_plugin_configuration_validates_effective_settings_and_identifies_origin(tmp_path: Path) -> None:
    """Merged plugin settings validate the effective value and identify the layer needing repair."""
    installation = tmp_path / "installation.yaml"
    installation.write_text("plugins:\n  identity-evidence:\n    provider_domains: PRIVATE_CONFIGURATION_VALUE\n")
    archive = tmp_path / "archive"
    save_archive_config(archive, ArchiveConfig(plugins={"identity-evidence": {"provider_domains": ["example.test"]}}))
    assert read_plugin_configuration(archive, "identity-evidence", installation).get_my_config()["provider_domains"] == ["example.test"]
    save_archive_config(archive, ArchiveConfig())
    with pytest.raises(ConfigurationError) as failure:
        read_plugin_configuration(archive, "identity-evidence", installation)
    assert failure.value.path == installation
    assert "plugins.identity-evidence.provider_domains" in str(failure.value)
    assert "PRIVATE_CONFIGURATION_VALUE" not in str(failure.value)
    installation.write_text("{}\n")
    save_archive_config(archive, ArchiveConfig(plugins={"identity-evidence": {"provider_domains": False}}))
    with pytest.raises(ConfigurationError) as failure:
        read_plugin_configuration(archive, "identity-evidence", installation)
    assert failure.value.path == archive / "config.yaml"


@pytest.mark.parametrize("content", [None, "", "{}\n", "imap_sources: []\n"])
def test_optional_configuration_defaults_are_read_only(tmp_path: Path, content: str | None) -> None:
    """Missing, empty and explicitly empty mapping/account lists retain defaults without writes."""
    path = tmp_path / "config.yaml"
    if content is not None:
        path.write_text(content)
    assert load_archive_config(tmp_path) == ArchiveConfig()
    assert path.exists() == (content is not None)
    if content is not None:
        assert path.read_text() == content


def test_configuration_file_access_problem_has_repair_guidance(tmp_path: Path) -> None:
    """A directory in place of the settings file reports an access problem without raw exception chains."""
    (tmp_path / "config.yaml").mkdir()
    with pytest.raises(ConfigurationError, match="cannot read settings") as failure:
        load_archive_config(tmp_path)
    assert "Repair config.yaml" in str(failure.value)


@pytest.mark.parametrize("content", ["plugins: PRIVATE_INSTALLATION_VALUE\n", "plugins: [PRIVATE_INSTALLATION_VALUE\n", "[]\n"])
def test_installation_configuration_uses_same_private_diagnostics(tmp_path: Path, content: str) -> None:
    """Installation settings use the same boundary and name the installation file in repair guidance."""
    path = tmp_path / "installation.yaml"
    path.write_text(content)
    with pytest.raises(ConfigurationError) as failure:
        load_installation_config(path)
    message = str(failure.value)
    assert "invalid installation configuration" in message and "Repair installation.yaml" in message
    assert "private_installation_value" not in message.casefold()
    assert "Traceback" not in format_failure(failure.value) and "ValidationError" not in message
    assert path.read_text() == content
