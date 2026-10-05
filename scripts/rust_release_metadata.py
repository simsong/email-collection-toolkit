# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Supply native Rust builds with the existing project's release identity.
# Read pyproject.toml and call the shared release-version mapper unchanged.
# Emit JSON for local build shells or append entries to GitHub's environment file.
# This is build metadata only; it neither builds nor publishes application files.
# Cargo verifies the mapped version matches the manifest before embedding a build.
# Windows feeds and signing keys remain separate explicit packaging inputs.
import argparse
import json
import os
from pathlib import Path
import tomllib

from mailarchiver.release_versions import release_metadata

VERSION = "ECT_RELEASE_VERSION"
BUILD = "ECT_RELEASE_BUILD"
CHANNEL = "ECT_RELEASE_CHANNEL"
GITHUB_ENV = "GITHUB_ENV"


def main() -> None:
    """Generate build configuration through the shared release mapper."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-env", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    _, channel, build, display = release_metadata(manifest["project"]["version"])
    metadata = {VERSION: display, BUILD: str(build), CHANNEL: channel}
    if args.github_env:
        with Path(os.environ[GITHUB_ENV]).open("a", encoding="utf-8", newline="\n") as stream:
            for name, value in metadata.items():
                stream.write(f"{name}={value}\n")
    else:
        print(json.dumps(metadata))


if __name__ == "__main__":
    main()
