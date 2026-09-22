# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Verify the real pinned Sparkle signer accepts the release key transport."""

import base64
from pathlib import Path

import pytest

from scripts.update_appcast import SPARKLE_PRIVATE_KEY_SECRET, signed_archive, signing_key


def test_real_signer_accepts_exported_seed_on_standard_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 32-byte exported seed signs an archive without a key file or Keychain access."""
    archive = tmp_path / "fixture.dmg"
    archive.write_bytes(b"synthetic Sparkle signing fixture")
    signer = Path(__file__).parents[1] / ".tools/sparkle/2.10.0/bin/sign_update"
    monkeypatch.setenv(SPARKLE_PRIVATE_KEY_SECRET, base64.b64encode(bytes(range(32))).decode("ascii"))

    result = signed_archive(archive, signer, signing_key())

    assert result.length == archive.stat().st_size
    assert len(base64.b64decode(result.signature, validate=True)) == 64
