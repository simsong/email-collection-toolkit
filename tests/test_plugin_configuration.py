# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""PLUGINS.md: real worker config fusion, scoped persistence and failure isolation."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mailarchiver.archive_config import ArchiveConfig, load_archive_config, save_archive_config
from mailarchiver.plugin_configuration import (
    ConfigWrite, apply_config_writes, load_installation_config,
    read_plugin_configuration, value_hash,
)
from mailarchiver.processing.api import RAW_MESSAGE, RunReport
from mailarchiver.processing.registry import fingerprint, load_processors
from mailarchiver.processing.runtime import run
from mailarchiver.processing.store import connect, submit

MESSAGE = Path(__file__).parent / "data/credible_date_with_quoted_body.eml"


def config_plugin(root: Path, code: str, *, timeout: float = 60) -> None:
    directory = root / "processors/config-reader"
    directory.mkdir(parents=True)
    directory.joinpath("plugin.toml").write_text(
        'api_version=2\nplugin_type="processor"\nkind="config-reader"\nname="Configuration test"\n'
        'implementation_version="1"\nentrypoint="plugin:create_plugin"\npipeline="ingest"\n'
        f'subscribes=["{RAW_MESSAGE}"]\nrank=1\ntimeout_seconds={timeout}\n')
    directory.joinpath("plugin.py").write_text(
        "from mailarchiver.processing.api import ProcessingResult\n"
        "import json\nimport time\n"
        "class Plugin:\n    def process(self, item):\n"
        + "".join(f"        {line}\n" for line in code.splitlines())
        + "\ndef create_plugin():\n    return Plugin()\n")


def test_plugin_configuration_deep_merge_and_snapshot_isolation(tmp_path: Path) -> None:
    installation = tmp_path / "install.yaml"
    installation.write_text(
        "version: 1\nplugins:\n  config-reader:\n    limit: 10\n"
        "    nested: {host: localhost, port: 12}\n    list: [a, b]\n    enabled: true\n"
        "  another: {secret: private}\n")
    archive = tmp_path / "archive"
    save_archive_config(archive, ArchiveConfig(plugins={"config-reader": {
        "limit": 3, "nested": {"port": 42}, "list": ["c"], "enabled": None,
    }}))
    settings = read_plugin_configuration(archive, "config-reader", installation)
    expected = {"limit": 3, "nested": {"host": "localhost", "port": 42}, "list": ["c"], "enabled": None}
    assert settings.get_my_config() == expected
    modified = settings.get_my_config()
    modified.clear()
    assert settings.get_my_config() == expected
    assert settings.get_my_config(scope="installation") != expected
    settings.write_my_config({}, scope="archive")
    assert settings.get_my_config() == settings.get_my_config(scope="installation")
    assert load_archive_config(archive).plugins["config-reader"]  # writes are staged
    assert read_plugin_configuration(archive, "absent", installation).get_my_config() == {}


def test_real_cli_writes_only_own_namespace_and_selected_scopes(tmp_path: Path) -> None:
    installation = tmp_path / "install.yaml"
    installation.write_text("version: 1\nplugins:\n  config-reader: {limit: 10, inherited: yes}\n  other: {keep: true}\n")
    config_plugin(tmp_path,
        'LIMIT = "limit"\nINHERITED = "inherited"\n'
        'assert item.get_my_config()[LIMIT] == 3\n'
        'assert item.get_my_config()[INHERITED] is True\n'
        'item.write_my_config({LIMIT: 5}, scope="archive")\n'
        'assert item.get_my_config()[LIMIT] == 5\n'
        'item.write_my_config({LIMIT: 12, INHERITED: True}, scope="installation")\n'
        'return ProcessingResult()')
    archive = tmp_path / "archive"
    command = [sys.executable, "-m", "mailarchiver.processing", "--archive", str(archive),
               "--plugin-dir", str(tmp_path), "--installation-config", str(installation)]
    subprocess.run([*command, "init"], check=True, capture_output=True, text=True)
    save_archive_config(archive, ArchiveConfig(last_import_directory=tmp_path, plugins={
        "config-reader": {"limit": 3}, "untouched": {"value": "keep"},
    }))
    subprocess.run([*command, "submit", str(MESSAGE)], check=True, capture_output=True, text=True)
    result = subprocess.run([*command, "run"], check=True, capture_output=True, text=True)
    assert RunReport.model_validate_json(result.stdout).completed == 1
    local = load_archive_config(archive)
    assert local.last_import_directory == tmp_path
    assert local.plugins == {"config-reader": {"limit": 5}, "untouched": {"value": "keep"}}
    assert load_installation_config(installation).plugins == {
        "config-reader": {"limit": 12, "inherited": True}, "other": {"keep": True},
    }
    # Production config updates also preserve plugin namespaces.
    local.last_import_directory = tmp_path / "next"
    save_archive_config(archive, local)
    assert load_archive_config(archive).plugins == local.plugins


@pytest.mark.parametrize("failure", ["timeout", "abort", "exception"])
def test_unsuccessful_plugin_never_commits_configuration(tmp_path: Path, failure: str) -> None:
    ending = {"timeout": "time.sleep(10)\nreturn ProcessingResult()",
              "abort": 'return ProcessingResult(outcome="abort-message")',
              "exception": 'raise ValueError("fixture failure")'}[failure]
    config_plugin(tmp_path, 'item.write_my_config({"new": True})\n' + ending,
                  timeout=1 if failure == "timeout" else 60)
    archive = tmp_path / "archive"
    installation = tmp_path / "install.yaml"
    plugins = load_processors((tmp_path,))
    with connect(archive, create=True) as database:
        submit(database, archive, MESSAGE, fingerprint(plugins))
        result = run(database, plugins, installation_config=installation)
        assert result.failed == 1 or result.aborted == 1
    assert not (archive / "config.yaml").exists()
    assert not installation.exists()


def test_concurrent_namespace_writes_preserve_other_plugins_and_detect_stale_values(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    installation = tmp_path / "install.yaml"
    first = ConfigWrite(scope="installation", values={"value": "first"}, expected_hash=value_hash({}))
    second = ConfigWrite(scope="installation", values={"value": "second"}, expected_hash=value_hash({}))
    apply_config_writes(archive, "one", (first,), installation)
    apply_config_writes(archive, "two", (second,), installation)
    apply_config_writes(archive, "one", (first,), installation)  # idempotent recovery
    assert load_installation_config(installation).plugins == {
        "one": {"value": "first"}, "two": {"value": "second"},
    }
    with pytest.raises(ValueError, match="concurrently"):
        apply_config_writes(archive, "one", (second,), installation)
    assert load_installation_config(installation).plugins["one"] == {"value": "first"}


@pytest.mark.parametrize("invalid", ["plugins: [not a mapping]\n", "plugins: [unterminated\n"])
def test_malformed_configuration_is_not_overwritten(tmp_path: Path, invalid: str) -> None:
    installation = tmp_path / "install.yaml"
    installation.write_text(invalid)
    with pytest.raises(ValueError):
        read_plugin_configuration(tmp_path / "archive", "one", installation)
    with pytest.raises(ValueError):
        apply_config_writes(tmp_path / "archive", "one",
                            (ConfigWrite(scope="installation", values={}, expected_hash=value_hash({})),), installation)
    assert installation.read_text() == invalid
