# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise a relocated Python MSIX with no checkout or developer-runtime imports.
# The package creates a new synthetic collection and uses shared reader services.
# A native WebView2 smoke traverses the actual JavaScript/Python promise bridge.
# Every synthetic archive file is hashed before and after native rendering.
# Installation and certificate trust remain separate, explicitly invoked checks.
# Native scanning must use this relocated package's own runtime and definitions.
"""Relocated Python webview MSIX acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import zipfile

from mailarchiver.reader_fixture import NATIVE_DESKTOP_TIMEOUT


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pst-fixture", type=Path, required=True)
    parser.add_argument("--ost-fixture", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    unpacked = output / "relocated"
    with zipfile.ZipFile(args.package) as package:
        package.extractall(unpacked)
    python = unpacked / "python/python.exe"
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("MAILARCHIVER", "MAIL_ARCHIVE", "PYTHON"))}
    environment["PYTHONPATH"] = str(output / "must-not-be-used")
    environment["APPDATA"] = str(output / "profile")
    environment["LOCALAPPDATA"] = str(output / "profile")
    check = output / "reader"
    code = ("import sys; from pathlib import Path; from mailarchiver.reader_fixture import check_reader; "
            "assert not any('must-not-be-used' in p for p in sys.path); check_reader(Path(sys.argv[1]))")
    subprocess.run([python, "-I", "-c", code, check], check=True, timeout=60, cwd=output, env=environment)
    scanner_code = ("from mailarchiver.clamav_definitions import selected_definitions; "
                    "from mailarchiver.clamav_update import validate_definitions; "
                    "definitions = selected_definitions(); "
                    "assert definitions.source == 'bundled', definitions; "
                    "validate_definitions(definitions); "
                    "print('Relocated packaged clean/EICAR scans passed:', definitions.versions)")
    subprocess.run([python, "-I", "-c", scanner_code], check=True, timeout=150, cwd=output, env=environment)
    # Only checked-in, synthetic/copied fixtures enter this fresh Outlook archive.
    outlook_sources = output / "outlook-sources"
    outlook_sources.mkdir()
    fixtures = [args.pst_fixture.resolve(strict=True), args.ost_fixture.resolve(strict=True)]
    hashes = []
    for fixture in fixtures:
        with fixture.open("rb") as source:
            hashes.append(hashlib.file_digest(source, "sha256").hexdigest())
        shutil.copyfile(fixture, outlook_sources / fixture.name)
    outlook_code = """
from pathlib import Path
import sys
from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.importer_tools import importer_executable
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.standalone_verify import verify_archive
from mailarchiver.gui_service import search_count
from mailarchiver.pff_source import PffReceipt
from mailarchiver.pst_source import ImportReceipt
root = Path(sys.argv[1])
for name in ('pst-importer', 'mcti-scan', 'pff-converter'):
    assert importer_executable(name).is_relative_to(Path(sys.executable).parent / 'importers')
for kind, expected_count, partial_error in (
        ('pst', 11, 'PST extraction incomplete'), ('ost', 80, 'libpff extraction incomplete;')):
    fixture, = (root / 'outlook-sources').glob('*.' + kind)
    request = IngestRequest(archive=root / (kind + '.mailarchive'), roots=[str(fixture)],
        owner_rules=OwnerRules(include=['*@example.test']), workers=1, scan_policy='clamav', index_attachments=True)
    for attempt in range(2):
        try:
            run_ingest(request, terminal=False)
        except RuntimeError as error:
            if not str(error).startswith(partial_error):
                raise
        else:
            raise AssertionError('The documented partial fixture unexpectedly completed: ' + kind)
        assert search_count(request.archive, '').total == expected_count
        assert not verify_archive(request.archive)
        if kind == 'ost':
            receipts = [PffReceipt.model_validate_json(path.read_bytes())
                        for path in (request.archive / 'processing-libpff').glob('*/receipt.json')]
            assert len(receipts) == attempt + 1
            assert all(item.emitted == 80 and item.errors == 1 and not item.complete for item in receipts)
        else:
            receipts = [ImportReceipt.model_validate_json(path.read_bytes())
                        for path in (request.archive / 'processing-pst').glob('*/receipt.json')]
            assert len(receipts) == attempt + 1
            assert all(item.emitted == 11 and item.exit_code == 1 for item in receipts)

"""
    with (output / "outlook-import.log").open("w", encoding="utf-8") as log:
        subprocess.run([python, "-I", "-c", outlook_code, output], check=True, timeout=180,
                       cwd=output, env=environment, stdout=log, stderr=subprocess.STDOUT)
    for fixture, expected in zip(fixtures, hashes, strict=True):
        for path in (fixture, outlook_sources / fixture.name):
            with path.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                    raise AssertionError("Outlook acquisition changed its input fixture")
    archive = check / "synthetic.mailarchive"
    fingerprint = "import sys,json; from pathlib import Path; from mailarchiver.reader_fixture import inventory; print(json.dumps(inventory(Path(sys.argv[1])),sort_keys=True))"
    def inventory() -> str:
        return subprocess.check_output([python, "-I", "-c", fingerprint, archive], text=True, timeout=30, cwd=output, env=environment)
    before = inventory()
    smoke = output / "native.json"
    with (output / "native.log").open("w", encoding="utf-8") as log:
        subprocess.run([python, "-I", "-m", "mailarchiver.desktop_entry", "--archive", archive,
                        "--smoke-test", "--smoke-html-find", "--smoke-report", smoke],
                       check=True, timeout=90, cwd=output, env=environment, stdout=log, stderr=subprocess.STDOUT)
    if not json.loads(smoke.read_text(encoding="utf-8"))["passed"]:
        raise AssertionError("Packaged native bridge failed")
    if inventory() != before:
        raise AssertionError("Native reader changed collection files")
    with (output / "desktop.log").open("w", encoding="utf-8") as log:
        subprocess.run([python, "-I", unpacked / "test_python_reader_native.py", "--desktop-check", check],
                       check=True, timeout=NATIVE_DESKTOP_TIMEOUT, cwd=output, env=environment, stdout=log, stderr=subprocess.STDOUT)
    if not json.loads((check / "desktop.json").read_text(encoding="utf-8"))["passed"]:
        raise AssertionError((check / "desktop.json").read_text(encoding="utf-8"))
    if inventory() != before:
        raise AssertionError("Packaged desktop interaction changed collection files")
    print(f"Relocated Python reader and native WebView2 passed: {output}")


if __name__ == "__main__":
    main()
