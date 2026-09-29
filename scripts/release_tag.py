#!/usr/bin/env python3
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Validate an Email Collection Toolkit release tag against pyproject metadata."""

from __future__ import annotations

import argparse
import subprocess
import tomllib
from pathlib import Path


from mailarchiver.release_versions import release_metadata


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
