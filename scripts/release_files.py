# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Stage the tested installers using the common release-version mapper.
# Authenticate downloaded Windows files against their streaming SHA-256 hashes.
# Require Windows public-trust verification evidence and exclude upgrade fixtures.
# Package the Microsoft-signed bundle without local certificate-trust helpers.
# Candidate CI and publication reuse the same assets and signing operations.
# No version, release tag or private signing material is stored in this script.
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tomllib
import zipfile
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter
from mailarchiver.release_versions import release_metadata

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


class FileHash(BaseModel):
    """PowerShell artifact hashes, independent of the build machine's paths."""
    algorithm: str = Field(alias="Algorithm")
    digest: str = Field(alias="Hash")
    path: str = Field(alias="Path")


class SigningVerification(BaseModel):
    """Windows CI evidence; staging verifies hashes, not Windows certificate trust."""
    signing_mode: Literal["public-trust"]
    publisher: str = Field(min_length=1)
    base_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timestamp_required: Literal[True]


def identity() -> tuple[str, str]:
    """Derive candidate identity from the canonical project version."""
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    tag, _, _, display = release_metadata(version)
    return tag, display


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stage(windows: Path, destination: Path) -> Path:
    """Copy the exact base payload verified by the Windows public signing gate."""
    hashes = TypeAdapter(list[FileHash]).validate_json((windows / "sha256.json").read_bytes())
    expected = {"base.msixbundle", "signing-verification.json"}
    if len(hashes) != len(expected):
        raise ValueError("Windows artifact must authenticate base bundle and public signing evidence")
    for item in hashes:
        name = item.path.replace("\\", "/").rsplit("/", 1)[-1]
        if name not in expected or item.algorithm != "SHA256" or digest(windows / name) != item.digest.lower():
            raise ValueError("Windows release artifact hash mismatch")
        expected.remove(name)
    verification = SigningVerification.model_validate_json((windows / "signing-verification.json").read_bytes())
    if verification.publisher.strip() == "CN=ECT Local Test" or verification.base_sha256 != digest(windows / "base.msixbundle"):
        raise ValueError("Windows public signing evidence differs from the release bundle")
    _, display = identity()
    destination.mkdir(parents=True, exist_ok=True)
    bundle = destination / f"ECT-{display}-windows-x64.msixbundle"
    shutil.copyfile(windows / "base.msixbundle", bundle)
    readme = (windows / "README.txt").read_text(encoding="utf-8").replace("base.msixbundle", bundle.name).replace("sha256.json", "SHA256SUMS")
    with zipfile.ZipFile(bundle.with_suffix(".zip"), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(bundle, bundle.name)
        archive.write(windows / "signing-verification.json", "signing-verification.json")
        archive.writestr("README.txt", readme)
        members = [bundle, windows / "signing-verification.json"]
        archive.writestr("SHA256SUMS", "".join(f"{digest(path)}  {path.name}\n" for path in members))
    files = sorted(path for path in destination.iterdir()
                   if path.name.endswith((".dmg", ".msixbundle", ".zip", ".tar.gz")))
    (destination / "SHA256SUMS").write_text("".join(f"{digest(path)}  {path.name}\n" for path in files), encoding="utf-8")
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("stage", "base", "sign"))
    parser.add_argument("--windows", type=Path, default=DIST / "windows")
    parser.add_argument("--appcast", type=Path)
    args = parser.parse_args()
    tag, display = identity()
    if args.operation == "stage":
        print(stage(args.windows, DIST))
        return
    if args.appcast is None:
        parser.error("--appcast is required for feed operations")
    if args.operation == "base":
        subprocess.run(["make", "release-appcast-base", f"RELEASE_TAG={tag}", f"APPCAST={args.appcast}"], cwd=ROOT, check=True)
        return
    dmgs = list(DIST.glob("*.dmg"))
    if len(dmgs) != 1 or "_UNSIGNED" in dmgs[0].name:
        raise ValueError("Exactly one signed and notarized candidate DMG is required")
    bundle = DIST / f"ECT-{display}-windows-x64.msixbundle"
    if not bundle.is_file():
        raise ValueError("The tested Windows release bundle is missing")
    prefix = f"https://github.com/simsong/email-collection-toolkit/releases/download/{tag}"
    subprocess.run(["make", "update-appcast", f"APPCAST={args.appcast}", f"RELEASE_TAG={tag}",
                    f"ARCHIVE={dmgs[0]}", f"RELEASE_URL={prefix}/{dmgs[0].name}",
                    f"WINDOWS_ARCHIVE={bundle}", f"WINDOWS_RELEASE_URL={prefix}/{bundle.name}"], cwd=ROOT, check=True)
    subprocess.run(["make", "check-appcast", f"APPCAST={args.appcast}", f"RELEASE_TAG={tag}",
                    "ARGS=--require-signed-feed --platform macos --platform windows"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
