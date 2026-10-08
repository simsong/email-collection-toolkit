# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Test updater exclusion against the real Python writer lease and Rust process.
# Both implementations use an isolated user's stable application lock file.
# Exercise blocked installation, installer reservation and cancellation release.
# Native delegate/termination probes create no windows or updater network checks.
# No application or certificate is installed.
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


@pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa shutdown policy")
def test_native_deferred_install_waits_for_cleanup_and_recovers_from_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Native update requirement: cleanup and external writers precede install; failure restores Quit."""
    configured = os.environ.get("RUST_WEBVIEW_BINARY")
    if not configured:
        pytest.skip("run make test-rust-updates")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    archive = tmp_path / "Shutdown.mailarchive"
    archive.mkdir()
    original = b"Original archive bytes\n"
    sentinel = archive / "source.eml"
    sentinel.write_bytes(original)
    lease = WriterLease.acquire(archive, "fixture", "test", "first", "fixture")
    with subprocess.Popen([configured, "--updater-shutdown-probe"], env={**os.environ, "TMPDIR": str(tmp_path)},
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as app:
        input_pipe, output_pipe = app.stdin, app.stdout
        assert input_pipe is not None and output_pipe is not None
        with selectors.DefaultSelector() as ready:
            ready.register(output_pipe, selectors.EVENT_READ)

            def reply(command: str | None, expected: str) -> None:
                if command is not None:
                    input_pipe.write(command + "\n")
                    input_pipe.flush()
                assert ready.select(timeout=15), "Native shutdown probe did not reply"
                result = output_pipe.readline().strip()
                assert result == expected, (result, app.stderr.read() if app.poll() is not None and app.stderr else "")

            try:
                reply(None, "waiting")
                reply("attempt", "waiting:0")  # no owner cleanup acknowledgment
                reply("ready", "waiting:0")
                reply("attempt", "waiting:0")  # actual external Python writer still holds fence
                lease.release()
                reply("attempt", "installing:1")
                with pytest.raises(ArchiveBusyError):
                    WriterLease.acquire(archive, "fixture", "test", "second", "fixture")
                reply("fail", "canceled:1")  # actual NSError delegate callback releases reservation
                lease = WriterLease.acquire(archive, "fixture", "test", "third", "fixture")
                lease.release()
                # Staging can install on ordinary Quit without a relaunch block.
                lease = WriterLease.acquire(archive, "fixture", "test", "staged", "fixture")
                reply("stage", "waiting:1")
                reply("attempt", "waiting:1")  # ordinary Quit also requires owner cleanup
                reply("ready", "waiting:1")
                reply("attempt", "waiting:1")  # external writer blocks staged termination
                lease.release()
                reply("attempt", "reserved-for-quit:1")  # no additional continuation call
                with pytest.raises(ArchiveBusyError):
                    WriterLease.acquire(archive, "fixture", "test", "staged-reserved", "fixture")
                reply("fail", "canceled:1")
                lease = WriterLease.acquire(archive, "fixture", "test", "staged-released", "fixture")
                lease.release()
                # Skip cancels the SDK installer, whereas Dismiss keeps install-on-Quit.
                lease = WriterLease.acquire(archive, "fixture", "test", "skipped", "fixture")
                reply("stage", "waiting:1")
                reply("dismiss", "waiting:1")
                reply("complete", "waiting:1")  # nil completion must retain Dismiss
                reply("skip", "waiting:1")  # cancellation has not completed yet
                reply("complete", "canceled:1")  # actual nil completion clears only Skip
                reply("attempt", "canceled:1")  # external writer cannot stall ordinary Quit
                reply("quit", "quit-canceled")
                lease.release()
                reply("quit", "quit-canceled")  # actual NSApplication termination returns to live process
                assert app.poll() is None
                _, stderr = app.communicate("done\n", timeout=15)
                assert app.returncode == 0, stderr
                assert sentinel.read_bytes() == original
            finally:
                lease.release()
                if app.poll() is None:
                    app.kill()
                    app.wait(timeout=15)
