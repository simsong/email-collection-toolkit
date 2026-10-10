# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the official Windows scanner with Python's SSL library already loaded.
# Validate clean streamed files and real EICAR memory samples concurrently.
# Check that input bytes remain intact and missing inputs are reported as errors.
# Verify the helper lifetime and hard startup deadline without replacing the DLL.
# These tests require the prepared x64 runtime and validated user definitions.
"""Requirements: isolated native dependencies, real verdicts, bounded worker lifetime."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes as c
from ctypes import wintypes as w
import hashlib
import os
from pathlib import Path
import ssl
import subprocess
import sys
from threading import Event
from time import monotonic

import pytest

from mailarchiver.clamav_update import EICAR
from mailarchiver.scanner import ClamScanner, ClamScannerStartupError, scanner_availability

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows DLL load-order regression")


def test_real_scanner_after_python_ssl_import(tmp_path: Path) -> None:
    assert ssl.OPENSSL_VERSION
    clean = tmp_path / "clean.eml"
    content = b"From: sender@example.test\nSubject: clean\n\nunaltered bytes\n"
    clean.write_bytes(content)
    digest = hashlib.sha256(content).digest()
    availability = scanner_availability()
    assert availability.configured, availability.detail
    with ClamScanner() as scanner:
        worker = scanner.process
        assert worker is not None and worker.is_alive()
        with ThreadPoolExecutor(max_workers=3) as pool:
            clean_result = pool.submit(scanner.scan, clean)
            infected_result = pool.submit(scanner.scan_sample, EICAR)
            missing_result = pool.submit(scanner.scan, tmp_path / "missing.eml")
            assert clean_result.result().status == "clean"
            infected = infected_result.result()
            assert infected.status == "infected" and "Eicar" in infected.detail
            assert infected.engine_version == availability.engine_version
            assert infected.signature_version == availability.definition_version
            assert missing_result.result().status == "scanner-error"
        assert hashlib.sha256(clean.read_bytes()).digest() == digest
    assert not worker.is_alive()


def test_windows_startup_deadline_reaps_native_worker() -> None:
    scanner = ClamScanner(startup_timeout_seconds=1e-9)
    with pytest.raises(ClamScannerStartupError, match="startup timed out"):
        scanner.__enter__()
    assert scanner.process is None and scanner.connection is None


def test_windows_scanner_exits_after_abrupt_owner_death(tmp_path: Path) -> None:
    """An inherited process handle, not PID polling, owns the native helper lifetime."""
    if sys.platform != "win32":
        pytest.skip("Windows process handles")
    marker = tmp_path / "worker-pid"
    code = ("from pathlib import Path\nfrom threading import Event\nimport sys\n"
            "from mailarchiver.scanner import ClamScanner\n"
            "with ClamScanner() as scanner:\n"
            " Path(sys.argv[1]).write_text(str(scanner.process.pid))\n Event().wait()\n")
    owner = subprocess.Popen([sys.executable, "-c", code, str(marker)])
    native = c.WinDLL("kernel32", use_last_error=True)
    native.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    native.OpenProcess.restype = w.HANDLE
    native.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    native.WaitForSingleObject.restype = w.DWORD
    native.CloseHandle.argtypes = [w.HANDLE]
    worker_handle = None
    try:
        deadline = monotonic() + 45
        while not marker.exists() and monotonic() < deadline:
            assert owner.poll() is None
            Event().wait(0.05)
        assert marker.exists(), "Scanner owner did not finish startup"
        worker_handle = native.OpenProcess(0x100000, False, int(marker.read_text()))
        assert worker_handle
        owner.kill()
        owner.wait(timeout=5)
        assert native.WaitForSingleObject(worker_handle, 5000) == 0
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if worker_handle:
            native.CloseHandle(worker_handle)
