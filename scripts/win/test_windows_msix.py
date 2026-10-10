# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise a relocated Python MSIX with no checkout or developer-runtime imports.
# The package creates a new synthetic collection and uses shared reader services.
# A native WebView2 smoke traverses the actual JavaScript/Python promise bridge.
# Every synthetic archive file is hashed before and after native rendering.
# Installation and certificate trust remain separate, explicitly invoked checks.
"""Relocated Python webview MSIX acceptance."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import zipfile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    unpacked = output / "relocated"
    with zipfile.ZipFile(args.package) as package:
        package.extractall(unpacked)
    python = unpacked / "python/python.exe"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(output / "must-not-be-used")
    environment["APPDATA"] = str(output / "profile")
    environment["LOCALAPPDATA"] = str(output / "profile")
    check = output / "reader"
    code = ("import sys; from pathlib import Path; from mailarchiver.reader_fixture import check_reader; "
            "assert not any('must-not-be-used' in p for p in sys.path); check_reader(Path(sys.argv[1]))")
    subprocess.run([python, "-I", "-c", code, check], check=True, timeout=60, cwd=output, env=environment)
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
                       check=True, timeout=90, cwd=output, env=environment, stdout=log, stderr=subprocess.STDOUT)
    if not json.loads((check / "desktop.json").read_text(encoding="utf-8"))["passed"]:
        raise AssertionError((check / "desktop.json").read_text(encoding="utf-8"))
    if inventory() != before:
        raise AssertionError("Packaged desktop interaction changed collection files")
    print(f"Relocated Python reader and native WebView2 passed: {output}")


if __name__ == "__main__":
    main()
