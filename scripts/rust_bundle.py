# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Assemble the primary Rust desktop with the frozen private archive service.
# Rust becomes the app entry point; the Python executable becomes a private peer.
# Preserve the application's identity, document ownership and Sparkle trust.
# Copy dependency license texts and record the candidate's source provenance.
# Mounted checks use only synthetic mail, real helper ingest and Rust RPC reads.
# No native windows, source mailbox mutations or installation occur here.
"""Rust desktop bundle assembly and headless installed-artifact checks."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import tempfile

from pydantic import BaseModel, Field, JsonValue

from mailarchiver.rust_engine import Capabilities
from mailarchiver.standalone_verify import verify_archive

APP_NAME = "Email Collection Toolkit"
EXECUTABLE = "mailsearch-webview"
SERVICE = "archive-service"
PLIST_EXECUTABLE = "CFBundleExecutable"
PLIST_NAME = "CFBundleName"
PLIST_DISPLAY = "CFBundleDisplayName"
PLIST_IDENTIFIER = "CFBundleIdentifier"
PLIST_DOCUMENTS = "CFBundleDocumentTypes"
PLIST_TYPES = "UTExportedTypeDeclarations"
PLIST_HANDLER_RANK = "LSHandlerRank"
PLIST_CONTENT_TYPES = "LSItemContentTypes"
PLIST_IS_PACKAGE = "LSTypeIsPackage"
PLIST_UTI = "UTTypeIdentifier"
PLIST_TAGS = "UTTypeTagSpecification"
PLIST_EXTENSION = "public.filename-extension"
REVISION = "revision"


class CargoPackage(BaseModel):
    name: str
    version: str
    license: str | None = None
    manifest_path: Path


class CargoMetadata(BaseModel):
    packages: list[CargoPackage]


class Provenance(BaseModel):
    baseline: str
    diff_sha256: str
    binary_sha256: str
    preview: bool = False


class RpcRequest(BaseModel):
    id: int
    method: str
    args: list[JsonValue] = Field(default_factory=list)


class RpcReply(BaseModel):
    id: int
    result: JsonValue = None
    error: str | None = None


class SearchRow(BaseModel):
    message_pk: int


class SearchResult(BaseModel):
    results: list[SearchRow]


class MessageMetadata(BaseModel):
    preferred_part_id: int


class BodyPart(BaseModel):
    kind: str
    content: str


class MacIntegration(BaseModel):
    choose_files: bool
    choose_directories: bool
    packages_as_directories: bool
    multiple: bool
    create_directories: bool
    bundle_icon_loaded: bool
    process_name: str


def copy_cargo_notices(notices: Path, root: Path) -> None:
    """Bundle the locked Cargo closure's upstream notices on either platform."""
    notices.mkdir(parents=True)
    metadata = CargoMetadata.model_validate_json(subprocess.check_output(
        [os.environ.get("CARGO", "cargo"), "metadata", "--locked", "--format-version", "1"], cwd=root,
    ))
    (notices / "dependencies.json").write_text(metadata.model_dump_json(indent=2) + "\n", encoding="utf-8")
    for package in metadata.packages:
        target = notices / f"{package.name}-{package.version}"
        source = package.manifest_path.parent
        for entry in source.iterdir():
            if entry.name.lower().startswith(("license", "copying", "notice")):
                target.mkdir(exist_ok=True)
                if entry.is_file():
                    shutil.copyfile(entry, target / entry.name)
                elif entry.is_dir():
                    shutil.copytree(entry, target / entry.name)


def prepare(app: Path, binary: Path, root: Path) -> Path:
    """Replace the launch executable while retaining PyInstaller's runtime layout."""
    plist = app / "Contents/Info.plist"
    info = plistlib.loads(plist.read_bytes())
    macos = app / "Contents/MacOS"
    (macos / info[PLIST_EXECUTABLE]).rename(macos / SERVICE)
    shutil.copy2(binary, macos / EXECUTABLE)
    info[PLIST_EXECUTABLE] = EXECUTABLE
    info[PLIST_NAME] = info[PLIST_DISPLAY] = APP_NAME
    # Rust replaces the Python entry point without changing updater identity.
    for document in info.get(PLIST_DOCUMENTS, []):
        document[PLIST_HANDLER_RANK] = "Owner"
    plist.write_bytes(plistlib.dumps(info))
    copy_cargo_notices(app / "Contents/Resources/Third Party Notices/Rust", root)
    provenance = Provenance(
        baseline=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        diff_sha256=sha256(subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=root)).hexdigest(),
        binary_sha256=sha256(binary.read_bytes()).hexdigest(),
    )
    (app / "Contents/Resources/rust-desktop.json").write_text(provenance.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return app


def check(app: Path, environment: dict[str, str]) -> list[str]:
    """Use frozen CLI ingest and Rust's actual private service discovery off-checkout."""
    service = app / "Contents/MacOS" / SERVICE
    binary = app / "Contents/MacOS" / EXECUTABLE
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    archive_types = {declaration[PLIST_UTI] for declaration in info.get(PLIST_TYPES, [])
                     if "mailarchive" in declaration.get(PLIST_TAGS, {}).get(PLIST_EXTENSION, [])}
    if not any(document.get(PLIST_IS_PACKAGE) and document.get(PLIST_HANDLER_RANK) == "Owner"
               and archive_types.intersection(document.get(PLIST_CONTENT_TYPES, []))
               for document in info.get(PLIST_DOCUMENTS, [])):
        raise RuntimeError("Rust desktop does not declare .mailarchive package document support")
    inspection = subprocess.run([str(binary), "--check-macos-integration"], cwd=app.parent, env=environment,
                                capture_output=True, text=True, check=True, timeout=30)
    native = MacIntegration.model_validate_json(inspection.stdout)
    if not (native.choose_files and native.choose_directories and native.bundle_icon_loaded
            and not native.packages_as_directories and not native.multiple and not native.create_directories
            and native.process_name == APP_NAME):
        raise RuntimeError(f"Packaged native document picker or application identity is incorrect: {native}")
    with tempfile.TemporaryDirectory(prefix="rust-dmg-check-") as temporary:
        work = Path(temporary)
        home = work / "home"
        home.mkdir()
        isolated = {**environment, "HOME": str(home), "ECT_RUST_ENGINE_PYTHON": str(work / "missing-python")}
        archive = work / "Fixture.mailarchive"
        source = work / "message.eml"
        raw = (b"From: owner@example.test\nTo: recipient@example.test\n"
               b"Date: Tue, 6 Oct 2026 10:00:00 +0000\nSubject: rustpackagefixture\n"
               b"Message-ID: <rustpackagefixture@example.test>\n\nPreserved package bytes\n>From quoted\n")
        source.write_bytes(raw)
        owners = work / "owners.txt"
        owners.write_text("owner@example.test\n", encoding="utf-8")
        command = [str(service), "--cli", "--archive", str(archive), "ingest", "--no-scan",
                   "--owner-names-file", str(owners), str(source)]
        subprocess.run(command, cwd=work, env=isolated, check=True, timeout=120)
        with sqlite3.connect(archive / "archive.sqlite3") as catalog:
            identifier, digest = catalog.execute("SELECT message_pk,sha256 FROM messages").fetchone()
        if digest != sha256(raw).hexdigest() or source.read_bytes() != raw or verify_archive(archive):
            raise RuntimeError("frozen ingest did not preserve source bytes and manifests")
        before = {path.relative_to(archive): sha256(path.read_bytes()).hexdigest()
                  for path in archive.rglob("*") if path.is_file()}
        requests = [RpcRequest(id=1, method="engine_status"), RpcRequest(id=2, method="options_status"),
                    RpcRequest(id=3, method="search", args=["rustpackagefixture"]),
                    RpcRequest(id=4, method="message", args=[identifier])]
        result = subprocess.run([str(binary), "--rpc", str(archive)], cwd=work, env=isolated,
                                input="".join(request.model_dump_json() + "\n" for request in requests),
                                text=True, capture_output=True, check=True, timeout=60)
        replies = [RpcReply.model_validate_json(line) for line in result.stdout.splitlines()]
        if len(replies) != 4 or any(reply.id != index or reply.error for index, reply in enumerate(replies, 1)):
            raise RuntimeError(f"packaged Rust RPC failed: {result.stdout}\n{result.stderr}")
        capability = Capabilities.model_validate(replies[0].result)
        if not capability.available or not capability.write_available:
            raise RuntimeError("Rust did not connect to the bundled Python service")
        options = replies[1].result
        if not isinstance(options, dict) or not options.get(REVISION):
            raise RuntimeError("packaged archive options service returned no revision")
        search = SearchResult.model_validate(replies[2].result)
        if [row.message_pk for row in search.results] != [identifier]:
            raise RuntimeError("packaged Rust search did not find the ingested message")
        metadata = MessageMetadata.model_validate(replies[3].result)
        body_request = RpcRequest(id=5, method="part", args=[identifier, metadata.preferred_part_id])
        body_response = subprocess.run([str(binary), "--rpc", str(archive)], cwd=work, env=isolated,
                                       input=body_request.model_dump_json() + "\n", text=True,
                                       capture_output=True, check=True, timeout=30)
        body_reply = RpcReply.model_validate_json(body_response.stdout.strip())
        if body_reply.id != 5 or body_reply.error:
            raise RuntimeError(f"packaged Rust body request failed: {body_reply}")
        body = BodyPart.model_validate(body_reply.result)
        if body.kind != "text" or body.content != "Preserved package bytes\n>From quoted\n":
            raise RuntimeError("packaged Rust reader did not display original message content")
        after = {path.relative_to(archive): sha256(path.read_bytes()).hexdigest()
                 for path in archive.rglob("*") if path.is_file()}
        if before != after:
            raise RuntimeError("packaged reader changed archive files")
        subprocess.run(command, cwd=work, env=isolated, check=True, timeout=120)
        with sqlite3.connect(archive / "archive.sqlite3") as catalog:
            if catalog.execute("SELECT count(*) FROM messages").fetchone()[0] != 1:
                raise RuntimeError("frozen repeat ingest duplicated the message")
        if source.read_bytes() != raw or verify_archive(archive):
            raise RuntimeError("frozen repeat ingest failed byte preservation")
    subprocess.run([str(binary), "--check-updater"], cwd=app.parent, env=environment,
                   capture_output=True, text=True, check=True, timeout=30)
    return ["Native Sparkle startup with mapped bundle metadata (no network check or windows)",
            "Rust packaged helper discovery with no checkout interpreter",
            "Native package-aware Open panel and bundled application name/icon (no windows)",
            "Rust packaged owner-options service and search/message reading",
            "Frozen synthetic ingest and idempotent repeat ingest",
            "Rust reader archive fixity and original source/manifest hashes"]
