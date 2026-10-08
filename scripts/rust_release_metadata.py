# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Supply native Rust builds with the existing project's release identity.
# Read pyproject.toml and call the shared release-version mapper unchanged.
# Emit JSON for local build shells or append entries to GitHub's environment file.
# Optionally run a build with these inputs; never publish application files.
# Cargo verifies the mapped version matches the manifest before embedding a build.
# Both platform adapters receive the existing shared feed and public signing key.
import argparse
import json
import os
import subprocess
from pathlib import Path
import tomllib

from mailarchiver.release_versions import release_metadata
from mailarchiver.update_metadata import SPARKLE_FEED_URL, SPARKLE_PUBLIC_KEY

VERSION = "ECT_RELEASE_VERSION"
BUILD = "ECT_RELEASE_BUILD"
CHANNEL = "ECT_RELEASE_CHANNEL"
FEED = "ECT_UPDATE_FEED_URL"
PUBLIC_KEY = "ECT_UPDATE_PUBLIC_KEY"
GITHUB_ENV = "GITHUB_ENV"


def main() -> None:
    """Generate build configuration through the shared release mapper."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-env", action="store_true")
    parser.add_argument("--build-command", nargs=argparse.REMAINDER,
                        help="run a build with mapped metadata in its private environment")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    _, channel, build, display = release_metadata(manifest["project"]["version"])
    metadata = {VERSION: display, BUILD: str(build), CHANNEL: channel,
                FEED: SPARKLE_FEED_URL, PUBLIC_KEY: SPARKLE_PUBLIC_KEY}
    if args.build_command:
        command = args.build_command
        if command[0] == "--":
            command = command[1:]
        if not command:
            parser.error("A build command is required")
        subprocess.run(command, env={**os.environ, **metadata}, check=True)
    elif args.github_env:
        with Path(os.environ[GITHUB_ENV]).open("a", encoding="utf-8", newline="\n") as stream:
            for name, value in metadata.items():
                stream.write(f"{name}={value}\n")
    else:
        print(json.dumps(metadata))


if __name__ == "__main__":
    main()
