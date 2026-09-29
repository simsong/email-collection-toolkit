# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Shared package, bundle, and Sparkle release version mapping."""

from packaging.version import InvalidVersion, Version

PREVIEW_CHANNEL = "preview"
RELEASE_CHANNEL = "release"
ALPHA = "a"
BETA = "b"
PREVIEW_STAGES = {ALPHA, BETA}


def release_metadata(version_text: str) -> tuple[str, str, int, str]:
    """Map one PEP 440 release version to its public tag, track, and Sparkle build."""
    try:
        version = Version(version_text)
    except InvalidVersion as error:
        raise ValueError(f"invalid PEP 440 version: {version_text}") from error
    if str(version) != version_text:
        raise ValueError("release version must use canonical PEP 440 spelling")
    if version.epoch or len(version.release) != 3 or version.dev or version.post or version.local:
        raise ValueError("releases must use MAJOR.MINOR.PATCH, optionally followed by aN or bN")
    major, minor, patch = version.release
    if any(value > 999 for value in version.release):
        raise ValueError("release components must be at most 999")
    base = major * 1_000_000_000 + minor * 1_000_000 + patch * 1_000
    if version.pre is None:
        return f"v{version}", RELEASE_CHANNEL, base + 900, str(version)
    stage, sequence = version.pre
    if stage not in PREVIEW_STAGES or not 1 <= sequence <= 399:
        raise ValueError("preview releases must use a1 through a399 or b1 through b399")
    offset = 100 if stage == ALPHA else 500
    return f"v{version}", PREVIEW_CHANNEL, base + offset + sequence, str(version)
