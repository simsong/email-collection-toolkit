# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Test updater exclusion against the real Python writer lease and Rust process.
# Both implementations use an isolated user's stable application lock file.
# Exercise blocked installation, installer reservation and cancellation release.
# No updater is initialized and no application or certificate is installed.
# Synthetic archive sentinels establish that the fence does not alter content.
# The Makefile builds the production Rust executable before running this module.
import os
from pathlib import Path
import subprocess
import tempfile
import selectors
import sys

import pytest

from mailarchiver.writer_lock import ArchiveBusyError, WriterLease


@pytest.mark.skipif(os.name == "nt", reason="Windows archive writing remains unsupported")
def test_rust_installation_excludes_python_writers_and_cancellation_releases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Update safety requirement: native installers and Python writers exclude each other."""
    configured = os.environ.get("RUST_WEBVIEW_BINARY")
    if not configured:
        pytest.skip("run make test-rust-updates")
    binary = Path(configured)
    # Configure only disposable temporary storage; flock and both processes are real.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    environment = {**os.environ, "TMPDIR": str(tmp_path)}
    archive = tmp_path / "Fixture.mailarchive"
    archive.mkdir()
    sentinel = archive / "source.eml"
    sentinel.write_bytes(b"From: fixture@example.test\n\nOriginal bytes\n")
    before = sentinel.read_bytes()
    lease = WriterLease.acquire(archive, "fixture", "test", "first", "fixture")
    try:
        result = subprocess.run([binary, "--updater-fence"], env=environment, capture_output=True,
                                text=True, input="", check=True, timeout=10)
        assert result.stdout.strip() == "blocked"
    finally:
        lease.release()
    with subprocess.Popen([binary, "--updater-fence"], env=environment, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as installer:
        try:
            assert installer.stdout is not None and installer.stdin is not None
            with selectors.DefaultSelector() as ready:
                ready.register(installer.stdout, selectors.EVENT_READ)
                assert ready.select(timeout=10), "Rust installation fence did not reply"
            assert installer.stdout.readline().strip() == "reserved"
            with pytest.raises(ArchiveBusyError):
                WriterLease.acquire(archive, "fixture", "test", "second", "fixture")
            stdout, stderr = installer.communicate("cancel\n", timeout=10)
            assert installer.returncode == 0, stderr
            assert stdout.strip() == "released"
        finally:
            if installer.poll() is None:
                installer.kill()
                installer.wait(timeout=10)
    lease = WriterLease.acquire(archive, "fixture", "test", "third", "fixture")
    lease.release()
    assert sentinel.read_bytes() == before
    if sys.platform == "darwin":
        # A real native exception must release the fence while Rust remains alive.
        with subprocess.Popen([binary, "--updater-fence"], env=environment, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as installer:
            try:
                assert installer.stdin is not None and installer.stdout is not None
                with selectors.DefaultSelector() as ready:
                    ready.register(installer.stdout, selectors.EVENT_READ)
                    assert ready.select(timeout=10)
                    assert installer.stdout.readline().strip() == "reserved"
                    installer.stdin.write("native-error\n")
                    installer.stdin.flush()
                    assert ready.select(timeout=10)
                    assert installer.stdout.readline().strip() == "released"
                assert installer.poll() is None
                lease = WriterLease.acquire(archive, "fixture", "test", "fourth", "fixture")
                lease.release()
                _, stderr = installer.communicate("done\n", timeout=10)
                assert installer.returncode == 0, stderr
            finally:
                if installer.poll() is None:
                    installer.kill()
                    installer.wait(timeout=10)
