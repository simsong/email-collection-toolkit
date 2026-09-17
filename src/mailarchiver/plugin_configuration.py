# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Plugin-owned configuration namespaces, merged snapshots and atomic scoped writes."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, PrivateAttr, TypeAdapter
from yaml import YAMLError, safe_dump, safe_load

from .application import application_preferences_path
from .archive_config import config_path, load_archive_config

ConfigValues = dict[str, JsonValue]
ConfigScope = Literal["archive", "installation"]
ReadScope = Literal["effective", "archive", "installation"]
VALUES = TypeAdapter(ConfigValues)


def value_hash(value: ConfigValues) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def merge_values(base: ConfigValues, override: ConfigValues) -> ConfigValues:
    """Recursively merge mappings; explicit nulls, lists and scalars replace defaults."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        prior = merged.get(key)
        merged[key] = merge_values(prior, value) if isinstance(prior, dict) and isinstance(value, dict) else copy.deepcopy(value)
    return merged


class ConfigWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: ConfigScope
    values: ConfigValues
    expected_hash: str


class PluginConfiguration(BaseModel):
    """One plugin's snapshot; pending writes never touch disk inside the worker."""
    model_config = ConfigDict(extra="forbid")
    installation: ConfigValues = Field(default_factory=dict)
    archive: ConfigValues = Field(default_factory=dict)
    _writes: list[ConfigWrite] = PrivateAttr(default_factory=list)

    def get_my_config(self, *, scope: ReadScope = "effective") -> ConfigValues:
        if scope == "effective":
            return merge_values(self.installation, self.archive)
        if scope not in ("archive", "installation"):
            raise ValueError("unknown configuration scope")
        return copy.deepcopy(self.archive if scope == "archive" else self.installation)

    def write_my_config(self, values: ConfigValues, *, scope: ConfigScope = "archive") -> None:
        """Replace this plugin's selected layer; {} removes its overrides."""
        validated = copy.deepcopy(VALUES.validate_python(values))
        value_hash(validated)  # Reject non-finite JSON numbers before any side effect.
        prior = self.get_my_config(scope=scope)
        self._writes.append(ConfigWrite(scope=scope, values=validated, expected_hash=value_hash(prior)))
        if scope == "archive":
            self.archive = validated
        else:
            self.installation = validated

    def pending_writes(self) -> tuple[ConfigWrite, ...]:
        return tuple(self._writes)


class InstallationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    plugins: dict[str, ConfigValues] = Field(default_factory=dict)


def installation_config_path() -> Path:
    """Writable per-installation/user settings, never the packaged defaults."""
    return application_preferences_path().with_name("config.yaml")


def load_installation_config(path: Path) -> InstallationConfig:
    try:
        value = safe_load(path.read_text(encoding="utf-8"))
        return InstallationConfig.model_validate(value if value is not None else {})
    except FileNotFoundError:
        return InstallationConfig()
    except (OSError, ValueError, TypeError, YAMLError) as error:
        raise ValueError(f"invalid installation configuration {path}: {error}") from error


def read_plugin_configuration(archive: Path, name: str, installation_path: Path | None = None) -> PluginConfiguration:
    installation = load_installation_config(installation_path or installation_config_path())
    local = load_archive_config(archive)
    return PluginConfiguration(installation=installation.plugins.get(name, {}), archive=local.plugins.get(name, {}))


def apply_config_writes(archive: Path, name: str, writes: tuple[ConfigWrite, ...],
                        installation_path: Path | None = None) -> None:
    """Preserve unrelated settings; detect stale same-plugin writes under a file lock."""
    # Collapse sequential writes to one replacement per scope with its original precondition.
    for scope in ("archive", "installation"):
        selected = [write for write in writes if write.scope == scope]
        if not selected:
            continue
        path = config_path(archive) if scope == "archive" else installation_path or installation_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_name(path.name + ".lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            config = load_archive_config(archive) if scope == "archive" else load_installation_config(path)
            current = config.plugins.get(name, {})
            desired = selected[-1].values
            if current == desired:
                continue  # Replay after interruption is idempotent.
            if value_hash(current) != selected[0].expected_hash:
                raise ValueError(f"configuration changed concurrently for {name} ({scope}); retry with a fresh snapshot")
            config.plugins[name] = desired
            descriptor, temporary = tempfile.mkstemp(prefix=".plugin-config-", dir=path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    safe_dump(config.model_dump(mode="json"), output, sort_keys=False, allow_unicode=True)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                Path(temporary).unlink(missing_ok=True)
