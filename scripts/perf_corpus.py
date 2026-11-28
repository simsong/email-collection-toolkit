# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Generate a deterministic synthetic email corpus for import performance tests.

Purpose: give every developer and CI run the same ~50 MB, many-small-message
input so import throughput can be compared across commits without private mail.
How it works: a seeded PRNG builds RFC 5322 messages (plain, multipart, base64
attachments, non-ASCII headers, threads, missing dates, duplicate Message-IDs)
and writes them as one-file-per-message Maildir plus a few MBOX files. Every
Subject and body carries a unique token, and 18 messages carry the committed
document attachments (two each of .txt, .pdf, .docx, .doc, .xls, .xlsx, .ppt,
.pptx, .jpeg), so ``benchmark_import.verify`` can check the archive end to end.
The same seed and size always produce byte-identical output (MANIFEST.json).
System role: input for ``scripts/benchmark_import.py`` (``make benchmark-import``).
All addresses use ``.test`` domains; no real mail is included.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

CORPUS_VERSION = 2
OWNER_MARKER = ".perf-corpus"
ATTACHMENTS = Path(__file__).parents[1] / "benchmarks" / "import" / "attachments"
# Message index of the first document attachment, and the spacing between them.
DOCUMENT_FIRST, DOCUMENT_STRIDE = 5, 7
WORDS = (
    "budget review meeting draft schedule quarterly archive contract invoice project "
    "update deadline travel proposal agenda minutes report forecast vendor release "
    "summary analysis feedback design network server backup policy security audit "
    "lunch thanks please attached question follow call notes data results plan"
).split()
FIRST = "avery blake casey devon emery finley gray harper indigo jordan kai logan morgan noel".split()
LAST = "abbot baker carver dalton ellis foster garner hayes irving jensen keller lowell".split()
DOMAINS = ("example.test", "corp.example.test", "mail.example.test", "univ.example.test")
# RFC 2047 encoded subjects and their decoded text.
NON_ASCII_SUBJECTS = (
    ("=?UTF-8?B?w5xiZXJzaWNodCBkZXIgS29zdGVu?=", "\u00dcbersicht der Kosten"),
    ("=?ISO-8859-1?Q?R=E9union_pr=E9vue?=", "R\u00e9union pr\u00e9vue"),
)


class CorpusManifest(BaseModel):
    corpus_version: int
    seed: int
    target_bytes: int
    message_count: int
    maildir_files: int
    mbox_files: int
    total_bytes: int
    sha256: str


class DocumentAttachment(BaseModel):
    """A committed attachment file (see scripts/make_benchmark_attachments.py)."""
    filename: str
    identifier: str
    mime_type: str
    sha256: str


class ExpectedAttachment(BaseModel):
    filename: str
    mime_type: str
    sha256: str
    identifier: str | None = None


class SyntheticMessage(BaseModel):
    """One generated message and the metadata an import must record for it."""
    index: int
    raw: bytes
    subject: str
    subject_token: str
    body_token: str
    sender: str
    recipients: list[str]
    date_utc: datetime | None
    received_utc: datetime
    message_id: str
    words: list[str]
    attachments: list[ExpectedAttachment]
    in_mbox: bool = False

    @property
    def archived(self) -> bytes:
        """Bytes the archive must keep: MBOX records keep their stored quoting.

        The MBOX files use the undeclared ``.mbox`` suffix, whose quoting
        variant is unknown, so the archive never unquotes them.
        """
        return mbox_quoted(self.raw) if self.in_mbox else self.raw


def document_attachments() -> list[DocumentAttachment]:
    return TypeAdapter(list[DocumentAttachment]).validate_json((ATTACHMENTS / "attachments.json").read_bytes())


def _person(rng: random.Random) -> tuple[str, str]:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    return f"{first.title()} {last.title()}", f"{first}.{last}@{rng.choice(DOMAINS)}"


def _paragraphs(rng: random.Random, count: int) -> str:
    return "\n\n".join(
        "\n".join(" ".join(rng.choice(WORDS) for _ in range(12)) for _ in range(rng.randint(2, 6)))
        for _ in range(count)
    )


def _attachment_part(boundary: str, filename: str, mime_type: str, payload: bytes) -> str:
    encoded = base64.encodebytes(payload).decode("ascii")
    return (f"--{boundary}\nContent-Type: {mime_type}; name=\"{filename}\"\n"
            f"Content-Disposition: attachment; filename=\"{filename}\"\n"
            f"Content-Transfer-Encoding: base64\n\n{encoded}")


def build_message(rng: random.Random, index: int, start: datetime, prior_ids: list[str],
                  document: DocumentAttachment | None = None) -> SyntheticMessage:
    """Return one synthetic message; the PRNG state and ``document`` fully determine its bytes."""
    name, sender = _person(rng)
    recipients = [_person(rng) for _ in range(rng.randint(1, 4))]
    when = start + timedelta(minutes=index * 7 + rng.randint(0, 6))
    message_id = f"<perf-{index:07d}.{rng.getrandbits(32):08x}@example.test>"
    if prior_ids and rng.random() < 0.01:
        message_id = rng.choice(prior_ids)  # duplicate Message-ID, different content
    subject_token, body_token = f"subjectz{index:06d}z", f"bodyz{index:06d}z"
    if rng.random() < 0.05:
        encoded, decoded = rng.choice(NON_ASCII_SUBJECTS)
        subject_header, subject = f"{encoded} {subject_token}", f"{decoded} {subject_token}"
    else:
        subject = subject_header = " ".join(rng.choice(WORDS) for _ in range(5)) + f" {subject_token}"
    headers = [
        f"Received: from relay{rng.randint(1, 9)}.example.test by mx.example.test; "
        f"{when.strftime('%a, %d %b %Y %H:%M:%S +0000')}",
        f"From: {name} <{sender}>",
        "To: " + ", ".join(f"{n} <{a}>" for n, a in recipients),
        f"Subject: {subject_header}",
        f"Message-ID: {message_id}",
        "MIME-Version: 1.0",
    ]
    dated = rng.random() > 0.01  # about 1% have no Date header
    if dated:
        headers.insert(1, f"Date: {when.strftime('%a, %d %b %Y %H:%M:%S +0000')}")
    if prior_ids and rng.random() < 0.4:
        parent = rng.choice(prior_ids[-50:])
        headers += [f"In-Reply-To: {parent}", f"References: {parent}"]
    body = _paragraphs(rng, rng.randint(1, 8)) + f"\n\nReference {body_token}"
    words = sorted(set(body.split()) - {"Reference", body_token})
    attachments: list[ExpectedAttachment] = []
    roll = rng.random()
    boundary = f"=_perf_{index:07d}"
    if roll < 0.70 and document is None:
        headers.append("Content-Type: text/plain; charset=us-ascii")
        text = body
    else:
        headers.append(f'Content-Type: multipart/mixed; boundary="{boundary}"')
        parts = [f"--{boundary}\nContent-Type: text/plain; charset=utf-8\n\n{body}\n"]
        if roll < 0.70:
            pass  # A plain message that carries only the document attachment.
        elif roll < 0.85:
            html = "<html><body><p>" + body.replace("\n\n", "</p><p>") + "</p></body></html>"
            parts.append(f"--{boundary}\nContent-Type: text/html; charset=utf-8\n\n{html}\n")
        else:
            payload = rng.randbytes(rng.choice((2_000, 8_000, 30_000, 120_000)))
            parts.append(_attachment_part(boundary, f"file{index}.bin", "application/octet-stream", payload))
            attachments.append(ExpectedAttachment(filename=f"file{index}.bin", mime_type="application/octet-stream",
                                                  sha256=hashlib.sha256(payload).hexdigest()))
        if document is not None:
            payload = (ATTACHMENTS / document.filename).read_bytes()
            parts.append(_attachment_part(boundary, document.filename, document.mime_type, payload))
            attachments.append(ExpectedAttachment(filename=document.filename, mime_type=document.mime_type,
                                                  sha256=document.sha256, identifier=document.identifier))
        text = "This is a multi-part message in MIME format.\n\n" + "".join(parts) + f"--{boundary}--\n"
    if rng.random() < 0.02:
        text += "\nFrom the desk of the archive team (mboxrd quoting case)\n"
    prior_ids.append(message_id)
    return SyntheticMessage(
        index=index, raw=("\n".join(headers) + "\n\n" + text + "\n").encode("utf-8"), subject=subject,
        subject_token=subject_token, body_token=body_token, sender=sender, recipients=[a for _n, a in recipients],
        date_utc=when if dated else None, received_utc=when, message_id=message_id, words=words, attachments=attachments)


def corpus_messages(target_bytes: int, seed: int) -> Iterator[SyntheticMessage]:
    """Yield the corpus's messages in order; ``generate`` writes exactly these bytes.

    The corpus continues past ``target_bytes`` until every document attachment
    is included, and its first fifth of bytes goes to MBOX files.
    """
    rng = random.Random(seed)
    start = datetime(2015, 1, 1, tzinfo=UTC)
    prior: list[str] = []
    documents = document_attachments()
    total = count = 0
    while total < target_bytes or count <= DOCUMENT_FIRST + DOCUMENT_STRIDE * (len(documents) - 1):
        slot, offset = divmod(count - DOCUMENT_FIRST, DOCUMENT_STRIDE)
        document = documents[slot] if count >= DOCUMENT_FIRST and offset == 0 and slot < len(documents) else None
        message = build_message(rng, count, start, prior, document)
        message.in_mbox = total < target_bytes // 5
        yield message
        total += len(message.raw)
        count += 1


def mbox_quoted(raw: bytes) -> bytes:
    """The record body as written to the corpus's ``.mbox`` files (mboxrd-style quoting)."""
    return b"\n".join(b">" + line if line.lstrip(b">").startswith(b"From ") else line for line in raw.split(b"\n"))


def generate(output: Path, target_bytes: int, seed: int) -> CorpusManifest:
    """Write the corpus under ``output`` (must not exist) and return its manifest."""
    output.mkdir(parents=True)
    # Written first, so an interrupted run is recognizably ours and safe to replace.
    (output / OWNER_MARKER).write_text("generated by scripts/perf_corpus.py\n", encoding="utf-8")
    maildir = output / "maildir" / "cur"
    maildir.mkdir(parents=True)
    (output / "maildir" / "new").mkdir()
    (output / "maildir" / "tmp").mkdir()
    mbox_dir = output / "mbox"
    mbox_dir.mkdir()
    digest = hashlib.sha256()
    total = count = maildir_files = 0
    mbox_index, mbox_bytes = 0, 0
    mbox_handle = None
    subjects: set[str] = set()
    for message in corpus_messages(target_bytes, seed):
        if message.subject in subjects:
            raise ValueError(f"duplicate generated Subject: {message.subject}")
        subjects.add(message.subject)
        raw = message.raw
        digest.update(raw)
        if message.in_mbox:
            if mbox_handle is None or mbox_bytes > 4 * 1024 * 1024:
                if mbox_handle is not None:
                    mbox_handle.close()
                mbox_index += 1
                mbox_handle = (mbox_dir / f"part{mbox_index:02d}.mbox").open("wb")
                mbox_bytes = 0
            record = b"From MAILER-DAEMON Thu Jan  1 00:00:00 2015\n" + mbox_quoted(raw) + b"\n"
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
