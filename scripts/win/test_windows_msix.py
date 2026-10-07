# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Validate the unpacked hybrid MSIX without a development Python dependency.
# Requirement: private helper discovery must survive relocation and isolated Python.
# Exercise real helper replies and the Rust bridge against synthetic archive bytes.
# Do not enable Windows archive writing or replace missing scanner/converter tools.
# Retain a machine-readable report alongside the generated fixture and package.
# Installation and certificate trust are deliberately separate acceptance steps.
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile


def inventory(root: Path) -> dict[str, str]:
    """Hash synthetic content before and after packaged read operations."""
    result = {}
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                result[str(path.relative_to(root))] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--installed-root", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    unpacked = args.installed_root or output / "relocated"
    if args.installed_root is None:
        with zipfile.ZipFile(args.package) as package:
            package.extractall(unpacked)
    python = unpacked / "python/python.exe"
    gui = unpacked / "mailsearch-webview.exe"
    archive = output / "synthetic.mailarchive"
    environment = os.environ.copy()
    environment.pop("ECT_RUST_ENGINE_PYTHON", None)
    environment["PYTHONPATH"] = str(output / "must-not-be-used")
    environment["LOCALAPPDATA"] = str(output / "profile")
    subprocess.run([unpacked / "mailsearch-rust.exe", "--create-demo", archive], check=True, timeout=30)
    before = inventory(archive)
    check = subprocess.run([python, "-I", "-c", "import sys,mailarchiver.rust_engine; print(sys.path)"],
                           capture_output=True, text=True, check=True, timeout=30, cwd=output, env=environment)
    assert "must-not-be-used" not in check.stdout
    replies = []
    bridge = subprocess.run([gui, "--rpc", archive],
                            input=json.dumps({"id": 1, "method": "engine_status", "args": []}) + "\n",
                            capture_output=True, text=True, check=True, timeout=30,
                            cwd=output, env=environment)
    reply = json.loads(bridge.stdout)
    replies.append(reply)
    assert reply.get("error") is None, reply
    assert reply["result"]["available"] is True, reply
    assert reply["result"]["write_available"] is False, reply
    runtime = subprocess.run([gui, "--check-webview"], capture_output=True, text=True,
                             check=True, timeout=30, env=environment)
    search = subprocess.run([gui, "--probe", archive, "observatory"], capture_output=True,
                            text=True, check=True, timeout=30, cwd=output, env=environment)
    assert "complete=true" in search.stdout, search.stdout
    assert inventory(archive) == before, "Packaged reader changed synthetic archive bytes"
    report = {"runtime": runtime.stdout, "helper": replies, "search": search.stdout,
              "archive_sha256": before, "installed_msix_tested": args.installed_root is not None}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output / "report.json")


if __name__ == "__main__":
    main()
