# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Generate a deterministic synthetic email corpus for import performance tests.

Purpose: give every developer and CI run the same ~50 MB, many-small-message
input so import throughput can be compared across commits without private mail.
How it works: a seeded PRNG builds RFC 5322 messages (plain, multipart, base64
attachments, non-ASCII headers, threads, missing dates, duplicate Message-IDs)
and writes them as one-file-per-message Maildir plus a few MBOX files. The same
seed and size always produce byte-identical output, recorded in MANIFEST.json.
System role: input for ``scripts/benchmark_import.py`` (``make benchmark-import``).
All addresses use ``.test`` domains; no real mail is included.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

CORPUS_VERSION = 1
WORDS = (
    "budget review meeting draft schedule quarterly archive contract invoice project "
    "update deadline travel proposal agenda minutes report forecast vendor release "
    "summary analysis feedback design network server backup policy security audit "
    "lunch thanks please attached question follow call notes data results plan"
).split()
FIRST = "avery blake casey devon emery finley gray harper indigo jordan kai logan morgan noel".split()
LAST = "abbot baker carver dalton ellis foster garner hayes irving jensen keller lowell".split()
DOMAINS = ("example.test", "corp.example.test", "mail.example.test", "univ.example.test")
NON_ASCII_SUBJECTS = ("=?UTF-8?B?w5xiZXJzaWNodCBkZXIgS29zdGVu?=", "=?ISO-8859-1?Q?R=E9union_pr=E9vue?=")


class CorpusManifest(BaseModel):
    corpus_version: int
    seed: int
    target_bytes: int
    message_count: int
    maildir_files: int
    mbox_files: int
    total_bytes: int
    sha256: str


def _person(rng: random.Random) -> tuple[str, str]:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    return f"{first.title()} {last.title()}", f"{first}.{last}@{rng.choice(DOMAINS)}"


def _paragraphs(rng: random.Random, count: int) -> str:
    return "\n\n".join(
        "\n".join(" ".join(rng.choice(WORDS) for _ in range(12)) for _ in range(rng.randint(2, 6)))
        for _ in range(count)
    )


def build_message(rng: random.Random, index: int, start: datetime, prior_ids: list[str]) -> bytes:
    """Return one synthetic message; the PRNG state fully determines its bytes."""
    name, sender = _person(rng)
    recipients = [_person(rng) for _ in range(rng.randint(1, 4))]
    when = start + timedelta(minutes=index * 7 + rng.randint(0, 6))
    message_id = f"<perf-{index:07d}.{rng.getrandbits(32):08x}@example.test>"
    if prior_ids and rng.random() < 0.01:
        message_id = rng.choice(prior_ids)  # duplicate Message-ID, different content
    subject = rng.choice(NON_ASCII_SUBJECTS) if rng.random() < 0.05 else " ".join(rng.choice(WORDS) for _ in range(5))
    headers = [
        f"Received: from relay{rng.randint(1, 9)}.example.test by mx.example.test; "
        f"{when.strftime('%a, %d %b %Y %H:%M:%S +0000')}",
        f"From: {name} <{sender}>",
        "To: " + ", ".join(f"{n} <{a}>" for n, a in recipients),
        f"Subject: {subject}",
        f"Message-ID: {message_id}",
        "MIME-Version: 1.0",
    ]
    if rng.random() > 0.01:  # about 1% have no Date header
        headers.insert(1, f"Date: {when.strftime('%a, %d %b %Y %H:%M:%S +0000')}")
    if prior_ids and rng.random() < 0.4:
        parent = rng.choice(prior_ids[-50:])
        headers += [f"In-Reply-To: {parent}", f"References: {parent}"]
    body = _paragraphs(rng, rng.randint(1, 8))
    roll = rng.random()
    if roll < 0.70:
        headers.append("Content-Type: text/plain; charset=us-ascii")
        text = body
    else:
        boundary = f"=_perf_{index:07d}"
        headers.append(f'Content-Type: multipart/mixed; boundary="{boundary}"')
        parts = [f"--{boundary}\nContent-Type: text/plain; charset=utf-8\n\n{body}\n"]
        if roll < 0.85:
            html = "<html><body><p>" + body.replace("\n\n", "</p><p>") + "</p></body></html>"
            parts.append(f"--{boundary}\nContent-Type: text/html; charset=utf-8\n\n{html}\n")
        else:
            payload = rng.randbytes(rng.choice((2_000, 8_000, 30_000, 120_000)))
            encoded = base64.encodebytes(payload).decode("ascii")
            parts.append(
                f"--{boundary}\nContent-Type: application/octet-stream; name=\"file{index}.bin\"\n"
                f"Content-Disposition: attachment; filename=\"file{index}.bin\"\n"
                f"Content-Transfer-Encoding: base64\n\n{encoded}"
            )
        text = "This is a multi-part message in MIME format.\n\n" + "".join(parts) + f"--{boundary}--\n"
    if rng.random() < 0.02:
        text += "\nFrom the desk of the archive team (mboxrd quoting case)\n"
    prior_ids.append(message_id)
    return ("\n".join(headers) + "\n\n" + text + "\n").encode("utf-8")


def generate(output: Path, target_bytes: int, seed: int) -> CorpusManifest:
    """Write the corpus under ``output`` (must not exist) and return its manifest."""
    output.mkdir(parents=True)
    maildir = output / "maildir" / "cur"
    maildir.mkdir(parents=True)
    (output / "maildir" / "new").mkdir()
    (output / "maildir" / "tmp").mkdir()
    mbox_dir = output / "mbox"
    mbox_dir.mkdir()
    rng = random.Random(seed)
    start = datetime(2015, 1, 1, tzinfo=UTC)
    prior: list[str] = []
    digest = hashlib.sha256()
    total = count = maildir_files = 0
    mbox_budget = target_bytes // 5  # 20% of bytes go to MBOX files, rest to Maildir
    mbox_index, mbox_bytes = 0, 0
    mbox_handle = None
    while total < target_bytes:
        raw = build_message(rng, count, start, prior)
        digest.update(raw)
        if total < mbox_budget:
            if mbox_handle is None or mbox_bytes > 4 * 1024 * 1024:
                if mbox_handle is not None:
                    mbox_handle.close()
                mbox_index += 1
                mbox_handle = (mbox_dir / f"part{mbox_index:02d}.mbox").open("wb")
                mbox_bytes = 0
            quoted = b"\n".join(
                b">" + line if line.lstrip(b">").startswith(b"From ") else line for line in raw.split(b"\n")
            )
            record = b"From MAILER-DAEMON Thu Jan  1 00:00:00 2015\n" + quoted + b"\n"
            mbox_handle.write(record)
            mbox_bytes += len(record)
        else:
            (maildir / f"{1420070400 + count}.{count:07d}.perf:2,S").write_bytes(raw)
            maildir_files += 1
        total += len(raw)
        count += 1
    if mbox_handle is not None:
        mbox_handle.close()
    manifest = CorpusManifest(
        corpus_version=CORPUS_VERSION, seed=seed, target_bytes=target_bytes, message_count=count,
        maildir_files=maildir_files, mbox_files=mbox_index, total_bytes=total, sha256=digest.hexdigest(),
    )
    (output / "MANIFEST.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--megabytes", type=float, default=50.0)
    parser.add_argument("--seed", type=int, default=20261122)
    arguments = parser.parse_args()
    manifest = generate(arguments.output, int(arguments.megabytes * 1024 * 1024), arguments.seed)
    print(json.dumps(manifest.model_dump()))


if __name__ == "__main__":
    main()
