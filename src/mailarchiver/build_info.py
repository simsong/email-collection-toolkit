# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Identify the source revision used by a desktop process or packaged application.
# Builders embed validated metadata before sealing their platform artifact.
# Development launches capture the owning checkout once when this module loads.
# About polls reuse that immutable snapshot even if the checkout later changes.
# Missing or invalid provenance is explicit; unrelated parent repositories are ignored.
# No network access or source/archive writes occur during runtime discovery.
"""Immutable Git provenance for desktop builds and development launches."""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

BUILD_INFO_FILENAME = "build-info.json"
GIT_ENVIRONMENT_PREFIX = "GIT_"


class BuildInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    git_commit: str | None = Field(default=None, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    dirty: bool | None = None


def checkout_build_info(root: Path) -> BuildInfo:
    """Read only this checkout, never a repository that happens to contain a package."""
    if not (root / ".git").exists():
        return BuildInfo()
    environment = {key: value for key, value in os.environ.items() if not key.startswith(GIT_ENVIRONMENT_PREFIX)}
    try:
        revision = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel", "HEAD"],
            capture_output=True, text=True, check=True, timeout=2, env=environment).stdout.splitlines()
        if len(revision) != 2 or Path(revision[0]).resolve() != root.resolve():
            return BuildInfo()
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True, text=True, check=True, timeout=2, env=environment).stdout
        return BuildInfo(git_commit=revision[1], dirty=bool(status))
    except (OSError, ValueError, subprocess.SubprocessError):
        return BuildInfo()


def load_build_info(package: Path) -> BuildInfo:
    """Embedded provenance takes precedence over development-checkout discovery."""
    embedded = package / BUILD_INFO_FILENAME
    if embedded.exists():
        try:
            return BuildInfo.model_validate_json(embedded.read_bytes())
        except (OSError, ValueError):
            return BuildInfo()
    return checkout_build_info(package.parent.parent)


def write_build_info(destination: Path, root: Path) -> BuildInfo:
    """Freeze provenance into a builder-owned file; never modify package sources."""
    info = checkout_build_info(root)
    if info.git_commit is None:
        raise ValueError("Build provenance requires a readable Git checkout")
    destination.write_text(info.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return info


BUILD_INFO = load_build_info(Path(__file__).resolve().parent)


def main() -> None:
    """Platform builders invoke this before copying/sealing their application."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    write_build_info(args.output, args.root)


if __name__ == "__main__":
    main()
