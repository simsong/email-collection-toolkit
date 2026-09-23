# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Optional Developer ID signing with a short-lived imported identity."""

from __future__ import annotations

import base64
import binascii
import os
import re
import secrets
import shlex
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr, ValidationError

CERTIFICATE_SECRET = "APPLE_CERTIFICATE_P12_BASE64"
PASSWORD_SECRET = "APPLE_CERTIFICATE_PASSWORD"
NOTARY_KEY_ID_SECRET = "APPLE_NOTARY_KEY_ID"
NOTARY_ISSUER_SECRET = "APPLE_NOTARY_ISSUER_ID"
NOTARY_PRIVATE_KEY_SECRET = "APPLE_NOTARY_PRIVATE_KEY_BASE64"
SPARKLE_PRIVATE_KEY_SECRET = "SPARKLE_ED25519_PRIVATE_KEY_BASE64"
PEM_PRIVATE_KEY_BEGIN = b"-----BEGIN PRIVATE KEY-----"
PEM_PRIVATE_KEY_END = b"-----END PRIVATE KEY-----"
RELEASE_SECRET_NAMES = (CERTIFICATE_SECRET, PASSWORD_SECRET, NOTARY_KEY_ID_SECRET, NOTARY_ISSUER_SECRET,
                        NOTARY_PRIVATE_KEY_SECRET, SPARKLE_PRIVATE_KEY_SECRET)
GITHUB_ACTIONS = "GITHUB_ACTIONS"
RUNNER_ENVIRONMENT = "RUNNER_ENVIRONMENT"
EXPLICIT_UNSIGNED_WARNING = (
    "::warning::Developer ID signing disabled by --signing-identity -. "
    "Producing _UNSIGNED.dmg with an ad-hoc-signed app; this build is not notarized."
)
UNSIGNED_WARNING = (
    "::warning::Developer ID signing skipped: both APPLE_CERTIFICATE_P12_BASE64 "
    "and APPLE_CERTIFICATE_PASSWORD are required. Producing _UNSIGNED.dmg "
    "with an ad-hoc-signed app; this build is not notarized."
)


class SigningSecrets(BaseModel):
    """GitHub environment boundary; secret values must never appear in reprs."""

    certificate: SecretStr = SecretStr("")
    password: SecretStr = SecretStr("")
    hosted_runner: bool = False

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> SigningSecrets:
        return cls(certificate=SecretStr(environment.get(CERTIFICATE_SECRET, "")),
                   password=SecretStr(environment.get(PASSWORD_SECRET, "")),
                   hosted_runner=(environment.get(GITHUB_ACTIONS) == "true"
                                  and environment.get(RUNNER_ENVIRONMENT) == "github-hosted"))

    @property
    def available(self) -> bool:
        return bool(self.certificate.get_secret_value().strip() and self.password.get_secret_value())

    def decode(self) -> bytes:
        """Accept wrapped Base64 while rejecting malformed configured credentials."""
        try:
            encoded = "".join(self.certificate.get_secret_value().split())
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Signing certificate secret is not valid Base64") from None
        if not decoded:
            raise ValueError("Signing certificate secret is empty")
        return decoded


def release_safe_environment(environment: Mapping[str, str], prefixes: tuple[str, ...]) -> dict[str, str]:
    """Keep release credentials and build-machine Python paths out of child processes."""
    return {key: value for key, value in environment.items()
            if key not in RELEASE_SECRET_NAMES and not key.startswith(prefixes)}


class NotarizationCredentials(BaseModel):
    """App Store Connect API-key material used only while submitting a DMG."""

    key_id: SecretStr = SecretStr("")
    issuer_id: SecretStr = SecretStr("")
    private_key: SecretStr = SecretStr("")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> NotarizationCredentials:
        return cls(key_id=SecretStr(environment.get(NOTARY_KEY_ID_SECRET, "")),
                   issuer_id=SecretStr(environment.get(NOTARY_ISSUER_SECRET, "")),
                   private_key=SecretStr(environment.get(NOTARY_PRIVATE_KEY_SECRET, "")))

    @property
    def available(self) -> bool:
        return all((self.key_id.get_secret_value().strip(), self.issuer_id.get_secret_value().strip(),
                    self.private_key.get_secret_value().strip()))

    def decoded_private_key(self) -> bytes:
        """Reject absent or malformed credentials before invoking Apple's tools."""
        if not self.available:
            raise RuntimeError("Apple notarization requires all protected App Store Connect API-key secrets")
        try:
            encoded = "".join(self.private_key.get_secret_value().split())
            private_key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Apple notarization private-key secret is not valid Base64") from None
        if not (private_key.startswith(PEM_PRIVATE_KEY_BEGIN)
                and private_key.rstrip().endswith(PEM_PRIVATE_KEY_END)):
            raise ValueError("Apple notarization private-key secret is not a PEM private key")
        return private_key


class NotarySubmission(BaseModel):
    id: str | None = None
    status: str | None = None


class NotaryIssue(BaseModel):
    path: str | None = None
    message: str | None = None


class NotaryLog(BaseModel):
    status_summary: str | None = Field(default=None, alias="statusSummary")
    issues: tuple[NotaryIssue, ...] | None = None


def notary_log_summary(output: str) -> str:
    """Report Apple's validation findings without printing the full service response."""
    try:
        report = NotaryLog.model_validate_json(output)
    except ValidationError:
        return "Apple did not provide a parseable notarization log"
    details = [f"{issue.path or 'archive'}: {issue.message}" for issue in report.issues or () if issue.message]
    return "; ".join(([report.status_summary] if report.status_summary else []) + details[:8]) or "No issue detail returned"


def redact_apple_text(message: str, credentials: NotarizationCredentials, key_path: Path) -> str:
    """Remove API-key material and paths from bounded Apple diagnostics."""
    message = message.replace(str(key_path), "[REDACTED KEY PATH]")
    for secret in (credentials.key_id, credentials.issuer_id, credentials.private_key):
        value = secret.get_secret_value().strip()
        if value:
            message = message.replace(value, "[REDACTED]")
    return message[:2048] or "No diagnostic returned"


def safe_apple_error(result: subprocess.CompletedProcess[str], credentials: NotarizationCredentials,
                     key_path: Path) -> str:
    """Show bounded tool errors while removing all API-key material and paths."""
    return redact_apple_text(result.stderr.strip() or result.stdout.strip(), credentials, key_path)


def developer_identity(output: str) -> str:
    """Select exactly one valid Developer ID Application key from the imported file."""
    identities = re.findall(r'^\s*\d+\) ([0-9A-Fa-f]{40}) "Developer ID Application: [^"\n]+"\s*$',
                            output, re.MULTILINE)
    if len(identities) != 1:
        raise ValueError("PKCS#12 must contain exactly one valid Developer ID Application signing identity")
    return identities[0]


def security_command(*arguments: str) -> str:
    """Omit argv/output from exceptions; argv remains visible to local processes."""
    try:
        result = subprocess.run(["/usr/bin/security", *arguments], check=False,
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Apple signing keychain operation could not complete") from None
    if result.returncode:
        raise RuntimeError(f"Apple signing keychain operation failed: {arguments[0]}")
    return result.stdout


@contextmanager
def signing_identity(credentials: SigningSecrets, work_root: Path,
                     explicit: str | None = None) -> Iterator[str]:
    """Restore the user's keychain search list and remove the imported key on exit."""
    if explicit is not None:
        if explicit == "-":
            print(EXPLICIT_UNSIGNED_WARNING, flush=True)
        yield explicit
        return
    if not credentials.available:
        print(UNSIGNED_WARNING, flush=True)
        yield "-"
        return
    if not credentials.hosted_runner:
        raise RuntimeError("Automatic PKCS#12 import requires an isolated GitHub-hosted runner; "
                           "use --signing-identity with an existing local keychain identity")
    certificate_bytes = credentials.decode()
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="signing-", dir=work_root) as temporary:
        certificate = Path(temporary) / "identity.p12"
        keychain = str(Path(temporary) / "signing.keychain-db")
        password = secrets.token_urlsafe(32)
        original = shlex.split(security_command("list-keychains", "-d", "user"))
        try:
            with certificate.open("xb") as handle:
                os.chmod(certificate, 0o600)
                handle.write(certificate_bytes)
            security_command("create-keychain", "-p", password, keychain)
            security_command("set-keychain-settings", "-lut", "21600", keychain)
            security_command("unlock-keychain", "-p", password, keychain)
            security_command("import", str(certificate), "-P", credentials.password.get_secret_value(),
                             "-k", keychain, "-T", "/usr/bin/codesign")
            certificate.unlink()
            security_command("set-key-partition-list", "-S", "apple-tool:,apple:,codesign:",
                             "-s", "-k", password, keychain)
            identity = developer_identity(security_command("find-identity", "-v", "-p", "codesigning", keychain))
            security_command("list-keychains", "-d", "user", "-s", keychain, *original)
            yield identity
        finally:
            try:
                security_command("list-keychains", "-d", "user", "-s", *original)
            finally:
                if Path(keychain).exists():
                    security_command("delete-keychain", keychain)


def dmg_filename(version: str, architecture: str, identity: str) -> str:
    """Unsigned artifacts must be identifiable independently of Actions logs."""
    suffix = "_UNSIGNED" if identity == "-" else ""
    return f"Email-Collection-Toolkit-{version}-{architecture}{suffix}.dmg"


def sign_image(image: Path, identity: str) -> None:
    """Sign and verify the finished container before publishing the candidate."""
    if identity != "-":
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", identity, "--timestamp", str(image)], check=True)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(image)], check=True)


def notarize_image(image: Path, credentials: NotarizationCredentials, work_root: Path) -> None:
    """Submit a signed DMG, staple Apple's ticket, and require Gatekeeper acceptance."""
    if not image.is_file() or image.suffix != ".dmg":
        raise ValueError("Apple notarization requires an existing DMG")
    private_key = credentials.decoded_private_key()
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="notary-", dir=work_root) as temporary:
        key_path = Path(temporary) / f"AuthKey_{credentials.key_id.get_secret_value().strip()}.p8"
        with key_path.open("xb") as handle:
            os.chmod(key_path, 0o600)
            handle.write(private_key)
        authentication = ["--key", str(key_path), "--key-id", credentials.key_id.get_secret_value().strip(),
                          "--issuer", credentials.issuer_id.get_secret_value().strip()]
        command = ["/usr/bin/xcrun", "notarytool", "submit", str(image), *authentication,
                   "--wait", "--output-format", "json"]
        stage = "notarization setup"
        try:
            stages = [("DMG signature verification", ["/usr/bin/codesign", "--verify", "--strict", str(image)], 120),
                      ("notarytool submission", command, 1800),
                      ("ticket stapling", ["/usr/bin/xcrun", "stapler", "staple", str(image)], 300),
                      ("ticket validation", ["/usr/bin/xcrun", "stapler", "validate", str(image)], 120),
                      ("Gatekeeper assessment", ["/usr/sbin/spctl", "--assess", "--type", "open",
                                                 "--context", "context:primary-signature", "--verbose=4", str(image)], 120)]
            for stage, arguments, timeout in stages:
                result = subprocess.run(arguments, check=False, capture_output=True, text=True, timeout=timeout)
                submission = NotarySubmission()
                if stage == "notarytool submission":
                    try:
                        submission = NotarySubmission.model_validate_json(result.stdout)
                    except ValidationError:
                        submission = NotarySubmission()
                    if submission.id and submission.status != "Accepted":
                        log = subprocess.run(["/usr/bin/xcrun", "notarytool", "log", submission.id, *authentication],
                                             check=False, capture_output=True, text=True, timeout=120)
                        detail = (notary_log_summary(log.stdout) if not log.returncode
                                  else safe_apple_error(log, credentials, key_path))
                        detail = redact_apple_text(detail, credentials, key_path)
                        raise RuntimeError(f"Apple notarization {submission.status or 'failed'} ({submission.id}): {detail}")
                if result.returncode or (stage == "notarytool submission" and submission.status != "Accepted"):
                    raise RuntimeError(f"{stage} failed (exit {result.returncode}): "
                                       f"{safe_apple_error(result, credentials, key_path)}")
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError(f"{stage} did not complete") from None
