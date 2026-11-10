# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Describe expected archive and installation settings problems for users.
# Configuration loaders keep strict validation and convert known failures here.
# Typed validator records supply field locations and explanations without inputs.
# YAML and file-access failures receive guidance without raw parser snippets.
# GUI and CLI boundaries can catch one exception and avoid traceback control flow.
# The affected filename remains explicit so repair instructions identify the right file.
"""Concise, value-free diagnostics shared by configuration consumers."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError
from yaml import YAMLError

ConfigurationScope = Literal["archive", "installation"]
VALIDATION_LOCATION = "loc"


def _display_text(value: str, limit: int) -> str:
    return " ".join("".join(char if char.isprintable() else " " for char in value).split())[:limit]


class ConfigurationError(ValueError):
    """An expected settings problem safe for user-facing diagnostics."""

    def __init__(self, path: Path, reason: str, scope: ConfigurationScope) -> None:
        self.path = path
        self.reason = _display_text(reason, 600).rstrip(".")
        super().__init__(f"invalid {scope} configuration {_display_text(str(path), 2048)}: {self.reason}. "
                         f"Repair {_display_text(path.name, 255)} and try again.")

    @classmethod
    def from_error(cls, path: Path, error: Exception, scope: ConfigurationScope, *, max_issues: int = 3) -> ConfigurationError:
        if isinstance(error, ValidationError):
            reason = _validation_reason(error, max_issues)
        elif isinstance(error, YAMLError):
            reason = "check YAML syntax and indentation"
        elif isinstance(error, OSError):
            reason = f"cannot read settings ({error.strerror or 'check file access'})"
        else:
            reason = "expected supported options in a YAML mapping"
        return cls(path, reason, scope)


class _ConfigurationIssue(BaseModel):
    """Read only setting locations and explanations from Pydantic's error records."""

    loc: tuple[str | int, ...]
    msg: str
    type: str


def _validation_reason(error: ValidationError, max_issues: int) -> str:
    issues = [_ConfigurationIssue.model_validate(issue) for issue in
              error.errors(include_input=False, include_context=False, include_url=False)[:max_issues]]
    details = []
    for issue in issues:
        field = ".".join(str(part) for part in issue.loc) or "settings"
        message = ("expected a list" if issue.type == "list_type" else
                   "expected a mapping of configuration options" if issue.type in {"model_type", "dict_type"} else
                   issue.msg.removeprefix("Value error, "))
        details.append(f"{field}: {message}")
    return "; ".join(details)
