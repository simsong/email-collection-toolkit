# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Define the shared worker policy used by application preferences and launchers.
# Radio choices retain a custom integer independently of the selected CPU ratio.
# Logical CPU detection supplies defaults and a maximum of twice the CPU count.
# Explicit input is validated; settings from larger machines are bounded on use.
# CLI overrides affect this launch without changing the persisted preference.
# UI snapshots expose the same limits and effective count as actual dispatch.
"""Typed worker limits shared by desktop and command-line entry points."""
from __future__ import annotations

import os
from argparse import ArgumentTypeError
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator

WorkerMode = Literal["half", "all", "custom"]
WORKER_MODES: tuple[WorkerMode, ...] = ("half", "all", "custom")


def logical_cpus() -> int:
    return os.cpu_count() or 1


def half_cpus() -> int:
    return max(1, logical_cpus() // 2)


def validate_workers(value: int, cpus: int | None = None) -> int:
    maximum = 2 * (cpus if cpus is not None else logical_cpus())
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"Workers must be a whole number from 1 to {maximum} (twice the CPU count).")
    return value


def worker_argument(value: str) -> int:
    try:
        return validate_workers(int(value))
    except ValueError as error:
        raise ArgumentTypeError(str(error)) from error


class WorkerPreferences(BaseModel):
    mode: WorkerMode = "half"
    custom_count: int = Field(default_factory=half_cpus, ge=1, strict=True)

    @classmethod
    def from_input(cls, mode: str, custom_count: int, cpus: int | None = None) -> WorkerPreferences:
        if mode not in WORKER_MODES:
            raise ValueError("Choose Half CPUs, All CPUs, or Custom.")
        result = cls(mode=mode, custom_count=custom_count)
        result.validate_limit(cpus)
        return result

    def resolve(self, cpus: int | None = None) -> int:
        count = cpus if cpus is not None else logical_cpus()
        if self.mode == "half":
            return max(1, count // 2)
        return count if self.mode == "all" else min(self.custom_count, 2 * count)

    def validate_limit(self, cpus: int | None = None) -> None:
        validate_workers(self.custom_count, cpus)


class WorkerSettings(BaseModel):
    preferences: WorkerPreferences
    cpus: int = Field(default_factory=logical_cpus, ge=1)
    command_line: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def check_override(self) -> WorkerSettings:
        if self.command_line is not None:
            validate_workers(self.command_line, self.cpus)
        return self

    @computed_field
    @property
    def maximum(self) -> int:
        return self.cpus * 2

    @computed_field
    @property
    def half(self) -> int:
        return max(1, self.cpus // 2)

    @computed_field
    @property
    def effective_count(self) -> int:
        return self.command_line if self.command_line is not None else self.preferences.resolve(self.cpus)

    @computed_field
    @property
    def override_note(self) -> str:
        return "" if self.command_line is None else f"# workers set to {self.command_line} on command line"
