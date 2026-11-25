# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Regenerate the benchmark corpus's document attachments with LibreOffice.

Purpose: give ``perf_corpus.py`` two real attachments of each common document
type (.txt, .pdf, .docx, .doc, .xls, .xlsx, .ppt, .pptx, .jpeg). Each contains
one unique identifier such as ``documentz07zpdfz1`` that a full-text index
keeps as a single word, so search results are self-validating.
How it works: write a flat OpenDocument (or CSV) source per attachment and run
``soffice --headless --convert-to`` on it. The JPEG is page one of a large-type
text document, so OCR can read its identifier. Results go to
``benchmarks/import/attachments/`` with ``attachments.json`` listing each file,
its identifier, MIME type and SHA-256.
System role: a one-time developer tool. Its outputs are committed, so the corpus
stays byte-identical on machines without LibreOffice. Never run during a benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
DEFAULT_OUTPUT = ROOT / "benchmarks" / "import" / "attachments"
# (extension, MIME type, LibreOffice source kind, conversion target)
TYPES = (
    ("txt", "text/plain", "text", ""),
    ("pdf", "application/pdf", "writer", "pdf"),
    ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "writer", "docx"),
    ("doc", "application/msword", "writer", "doc"),
    ("xls", "application/vnd.ms-excel", "calc", "xls"),
    ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "calc", "xlsx"),
    ("ppt", "application/vnd.ms-powerpoint", "impress", "ppt"),
    ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "impress", "pptx"),
    ("jpeg", "image/jpeg", "writer", "jpg"),
)
COPIES = 2
OFFICE = 'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
WRITER = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document {OFFICE} xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
 office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.text">
<office:automatic-styles><style:style style:name="P1" style:family="paragraph">
<style:text-properties fo:font-size="28pt"/></style:style></office:automatic-styles>
<office:body><office:text><text:p text:style-name="P1">{{text}}</text:p></office:text></office:body></office:document>
"""
IMPRESS = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document {OFFICE} xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 office:version="1.2" office:mimetype="application/vnd.oasis.opendocument.presentation">
<office:body><office:presentation><draw:page draw:name="page1">
<draw:frame svg:x="2cm" svg:y="2cm" svg:width="22cm" svg:height="6cm"><draw:text-box>
<text:p>{{text}}</text:p></draw:text-box></draw:frame></draw:page></office:presentation></office:body></office:document>
"""


def identifier(number: int, extension: str, copy: int) -> str:
    """A single FTS word: letters and digits only, since '_', '-' and '^' split tokens."""
    return f"documentz{number:02d}z{extension}z{copy}"


def _convert(source: Path, target: str, directory: Path) -> Path:
    subprocess.run(["soffice", "--headless", "--convert-to", target, "--outdir", str(directory), str(source)],
                   check=True, capture_output=True, timeout=180)
    produced = directory / f"{source.stem}.{target}"
    if not produced.is_file():
        raise RuntimeError(f"LibreOffice did not produce {produced.name}")
    return produced


def generate(output: Path) -> list[dict[str, str]]:
    output.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, str]] = []
    number = 0
    with tempfile.TemporaryDirectory(prefix="bench-attachments-") as scratch:
        work = Path(scratch)
        for extension, mime, kind, target in TYPES:
            for copy in range(1, COPIES + 1):
                number += 1
                token = identifier(number, extension, copy)
                text = f"Archive benchmark attachment {token} prepared for the records review."
                destination = output / f"{token}.{extension}"
                if kind == "text":
                    destination.write_text(text + "\n", encoding="ascii")
                else:
                    source = work / token
                    if kind == "writer":
                        source = source.with_suffix(".fodt")
                        source.write_text(WRITER.format(text=text), encoding="utf-8")
                    elif kind == "impress":
                        source = source.with_suffix(".fodp")
                        source.write_text(IMPRESS.format(text=text), encoding="utf-8")
                    else:
                        source = source.with_suffix(".csv")
                        source.write_text(f"field,value\nidentifier,{token}\nnote,{text}\n", encoding="ascii")
                    shutil.move(_convert(source, target, work), destination)
                entries.append({"filename": destination.name, "identifier": token, "mime_type": mime,
                                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
    (output / "attachments.json").write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    for entry in generate(parser.parse_args().output):
        print(entry["filename"])


if __name__ == "__main__":
    main()
