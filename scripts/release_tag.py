#!/usr/bin/env python3
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Validate an Email Collection Toolkit release tag against pyproject metadata."""

from __future__ import annotations

import argparse
import subprocess
import tomllib
from pathlib import Path

from packaging.version import InvalidVersion, Version

PREVIEW_CHANNEL = "preview"
RELEASE_CHANNEL = "release"
ALPHA = "a"
BETA = "b"
CHANNEL_STAGES = {ALPHA: "alpha", BETA: "beta"}


def release_metadata(version_text: str) -> tuple[str, str, int, str]:
    """Map one PEP 440 release version to its public tag, track, and Sparkle build."""
    try:
        version = Version(version_text)
    except InvalidVersion as error:
        raise ValueError(f"invalid PEP 440 version: {version_text}") from error
    if version.epoch or len(version.release) != 3 or version.dev or version.post or version.local:
        raise ValueError("releases must use MAJOR.MINOR.PATCH, optionally followed by aN or bN")
    major, minor, patch = version.release
    if any(value > 999 for value in version.release):
        raise ValueError("release components must be at most 999")
    base = major * 1_000_000_000 + minor * 1_000_000 + patch * 1_000
    if version.pre is None:
        return f"v{version}", RELEASE_CHANNEL, base + 900, str(version)
    stage, sequence = version.pre
    if stage not in CHANNEL_STAGES or not 1 <= sequence <= 399:
        raise ValueError("preview releases must use a1 through a399 or b1 through b399")
    label = CHANNEL_STAGES[stage]
    offset = 100 if stage == ALPHA else 500
    return f"v{major}.{minor}.{patch}-{label}.{sequence}", PREVIEW_CHANNEL, base + offset + sequence, \
        f"{major}.{minor}.{patch}-{label}.{sequence}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--require-annotated", action="store_true")
    args = parser.parse_args()
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    try:
        expected, channel, sparkle_version, display_version = release_metadata(version)
    except ValueError as error:
        parser.error(str(error))
    if args.tag != expected:
        parser.error(f"{args.tag} does not match pyproject version {version}; expected {expected}")
    if args.require_annotated:
        tag_type = subprocess.run(
            ["git", "cat-file", "-t", f"refs/tags/{args.tag}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if tag_type != "tag":
            parser.error(f"{args.tag} must be an annotated tag")
    print(f"version={version} tag={args.tag} channel={channel} sparkle_version={sparkle_version} "
          f"display_version={display_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
