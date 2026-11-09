# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Test standalone IMAP acquisition against an actual disposable TLS mail server.
# Verify LF normalization, wire provenance, attachments, source flags and durable reruns.
# Exercise both TLS modes and real CLI/background-GUI ingestion without native windows.
# Require downloaded bodies and attachments to reach the production search service.
# Gmail-specific response parsing and account-scoped identities use recorded metadata.
# Only the OS credential boundary is injected to protect the user's native keychain.
# These tests establish local behavior, not live Gmail or native Windows acceptance.
"""Requirements: read-only resumable IMAP, correct counters and standalone CLI operation."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.parser import BytesParser
import errno
import hashlib
import imaplib
import json
from pathlib import Path
import sqlite3
import os
import signal
import subprocess
import sys
import time
import traceback
from threading import Barrier, Event

import pytest

from mailarchiver.file_events import wait_for_path_state
from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.application import create_empty_archive
from mailarchiver.archive_config import load_archive_config, save_archive_config
from mailarchiver.document_options import OWNER_NAMES_DETECTED
from mailarchiver.imap_cli import SyncReporter, main as imap_main, parser, synchronize
from mailarchiver.imap_credentials import save_password
from mailarchiver.imap_credentials import PASSWORD_KEY, password_for, _development_password
from keyring.errors import KeyringError
from mailarchiver.imap_models import ImapAccount, ImapDownload, ImapMailbox, ImapMessageMetadata
from mailarchiver.imap_cli import prompt_account
from mailarchiver.imap_protocol import ImapAuthenticationError, ImapPaused, ImapServerBusy, ImapSession, body_literal, connection_interrupted, decode_mailbox, message_metadata, quoted, response_tokens, selected_mailboxes
from mailarchiver.imap_state import initialize_state, known_observation, record_download, refresh_observation, totals
from mailarchiver.gui_processing import resume_request, unfinished_work
from mailarchiver.gui_app import IngestWindowApi
from mailarchiver.writer_lock import WriterLease
from mailarchiver.gui_service import SearchPage, search_page
from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.ingest_diagnostics import format_failure
from mailarchiver.imap_source import ImapSourcePlugin, imap_locator
from mailarchiver.standalone_verify import verify_archive
from tests.test_gui_processing import open_headless, wait_for_import
from mailarchiver.mbox import MboxLocation, read_verified_location
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.plugin_api import MailContainer, MailObject, ProgressEvent, SourceObservation, SourceReference, SourceSpec

from tests.imap_fixture import DisposableImap, FixtureKeyring, ImapFault, InterruptibleImap, Utf8Imap

SSL_CERT_FILE_ENV = "SSL_CERT_FILE"


@pytest.mark.parametrize("plain", [False, True])
@pytest.mark.parametrize("reply,temporary", [
    (b"NO [AUTHENTICATIONFAILED] Invalid credentials", False),
    (b"NO [ALERT] Too many simultaneous connections. (Failure)", True),
    (b"NO [UNAVAILABLE] Try later", True),
    (b"NO unexpected server policy", False),
])
def test_login_rejections_preserve_reason_and_distinguish_limits(tmp_path: Path, plain: bool,
        reply: bytes, temporary: bool) -> None:
    """ACQUISITION: real LOGIN/PLAIN replies distinguish server limits from rejected credentials."""
    server = Utf8Imap(tmp_path, credentials=("demouser", "secret") if plain else None, login_response=reply)
    try:
        account = server.start()
        expected = ImapPaused if temporary else ImapAuthenticationError
        with pytest.raises(expected) as failure:
            with ImapSession(account, "secret", server.context, authentication_only=True):
                pytest.fail("server rejection was ignored")
        assert reply.removeprefix(b"NO ").decode() in str(failure.value)
        assert "secret" not in str(failure.value)
        assert not any(b" LIST " in command or b" EXAMINE " in command for command in server.commands)
    finally:
        server.close()


@pytest.mark.parametrize("plain,password", [
    (False, 'synthetic-"private\\secret'), (True, 'synthetic-"private\\secret'),
    (True, "synthetic-λ'\\secret"),
])
@pytest.mark.parametrize("status", [b"NO", b"BAD", b"broken"])
@pytest.mark.parametrize("wire", [False, True], ids=["raw-echo", "wire-echo"])
def test_server_echoed_credentials_are_redacted(tmp_path: Path, plain: bool, password: str, status: bytes, wire: bool) -> None:
    """ACQUISITION: ordinary, BAD and malformed replies cannot leak raw/repr credentials or SASL payloads."""
    import base64
    payload = base64.b64encode(b"\0demouser\0" + password.encode())
    echo = quoted(password.encode()) if wire else password.encode()
    reply = status + b" [AUTHENTICATIONFAILED] " + echo + b" " + payload
    if status == b"broken":
        reply += b" too many connections"  # Malformed protocol data must never become retryable.
    server = Utf8Imap(tmp_path, credentials=("demouser", password) if plain else None, login_response=reply)
    try:
        account = server.start()
        expected = imaplib.IMAP4.abort if status == b"broken" else ImapAuthenticationError
        with pytest.raises(expected) as failure:
            with ImapSession(account, password, server.context, authentication_only=True):
                pytest.fail("server rejection was ignored")
        diagnostic = "\n".join((str(failure.value), format_failure(failure.value),
                                "".join(traceback.format_exception(failure.value))))
        for value in (password, password.replace("\\", "\\\\").replace('"', '\\"')):
            escaped = "".join(repr(bytes([byte]))[2:-1] for byte in value.encode())
            for secret in (value, escaped, escaped.replace("'", "\\'"), escaped.replace('"', '\\"'), payload.decode()):
                assert secret not in diagnostic
        assert "[AUTHENTICATIONFAILED]" in str(failure.value) and "[redacted]" in str(failure.value)
        assert failure.value.__cause__ is None and failure.value.__suppress_context__
    finally:
        server.close()


def test_connection_limit_in_greeting_is_not_a_password_error(tmp_path: Path) -> None:
    """ACQUISITION: capacity rejection can precede authentication and must remain retryable."""
    server = Utf8Imap(tmp_path, greeting=b"* BYE [ALERT] Too many simultaneous connections.\r\n")
    try:
        account = server.start()
        with pytest.raises(ImapServerBusy, match="Too many simultaneous connections"):
            with ImapSession(account, "demopass", server.context):
                pytest.fail("connection refusal was ignored")
        assert not server.commands
    finally:
        server.close()


@pytest.mark.parametrize("tls", ["implicit", "starttls"])
@pytest.mark.parametrize("greeting", [b"* NO [UNAVAILABLE] too many connections demopass\r\n",
                                    b"* BAD [LIMIT] wrong greeting demopass\r\n"])
def test_connection_limit_words_in_malformed_greetings_are_not_retryable(tmp_path: Path,
        tls: str, greeting: bytes) -> None:
    """ACQUISITION: invalid NO/BAD greetings fail promptly despite capacity wording."""
    server = Utf8Imap(tmp_path, tls="implicit" if tls == "implicit" else "starttls", greeting=greeting)
    try:
        account = server.start()
        archive = tmp_path / "config-only"
        save_archive_config(archive, load_archive_config(archive).model_copy(update={"imap_sources": [account]}))
        plugin = ImapSourcePlugin(credentials=lambda _account: "demopass", context=server.context)
        with pytest.raises(imaplib.IMAP4.error) as failure:
            next(plugin.discover(SourceSpec(locator=imap_locator(archive, account, 1))))
        assert "demopass" not in str(failure.value) and "[redacted]" in str(failure.value)
        assert not server.commands
    finally:
        server.close()


def test_connection_backoff_retries_discovery_until_server_accepts(tmp_path: Path) -> None:
    """ACQUISITION: repeated real TLS refusals back off for 1s then 2s without ending discovery."""
    server = Utf8Imap(tmp_path, credentials=("demouser", "demopass"),
                      login_response=b"NO [ALERT] Too many simultaneous connections.", login_failures=2)
    try:
        account = server.start()
        archive = tmp_path / "config-only"
        save_archive_config(archive, load_archive_config(archive).model_copy(update={"imap_sources": [account]}))
        plugin = ImapSourcePlugin(credentials=lambda _account: "demopass", context=server.context)
        with closing(plugin.discover(SourceSpec(locator=imap_locator(archive, account, 1)))) as discovered:
            first = next(discovered)
            assert isinstance(first, ProgressEvent) and "retrying in 1s" in first.phase
            started = time.monotonic()
            second = next(discovered)
            assert isinstance(second, ProgressEvent) and "retrying in 2s" in second.phase
            assert time.monotonic() - started >= 1
            assert isinstance(next(discovered), MailContainer)
            assert time.monotonic() - started >= 3
        assert sum(b" AUTHENTICATE PLAIN" in command for command in server.commands) == 3
        assert set(archive.iterdir()) == {archive / "config.yaml"}
    finally:
        server.close()


@pytest.mark.parametrize("command,authenticated,tls,reply", [
    (b"CAPABILITY", False, "implicit", b"NO"), (b"CAPABILITY", True, "implicit", b"NO"),
    (b"ENABLE", True, "implicit", b"NO"), (b"CAPABILITY", False, "implicit", b"BYE"),
    (b"CAPABILITY", True, "implicit", b"BYE"), (b"STARTTLS", False, "starttls", b"NO"),
    (b"STARTTLS", False, "starttls", b"BYE"), (b"CAPABILITY", False, "starttls", b"NO"),
    (b"CAPABILITY", False, "starttls", b"BYE"),
])
def test_connection_backoff_retries_session_setup_refusals(tmp_path: Path, command: bytes, authenticated: bool,
        tls: str, reply: bytes) -> None:
    """ACQUISITION: real NO/BYE refusals before/after TLS or login retry without misclassifying protocol errors."""
    assert tls in {"implicit", "starttls"}
    server = Utf8Imap(tmp_path, credentials=("demouser", "demopass"), setup_command=command,
                      setup_response=reply + b" [UNAVAILABLE] Temporarily unavailable", setup_authenticated=authenticated,
                      tls="starttls" if tls == "starttls" else "implicit")
    try:
        account = server.start()
        archive = tmp_path / "config-only"
        save_archive_config(archive, load_archive_config(archive).model_copy(update={"imap_sources": [account]}))
        plugin = ImapSourcePlugin(credentials=lambda _account: "demopass", context=server.context)
        with closing(plugin.discover(SourceSpec(locator=imap_locator(archive, account, 1)))) as discovered:
            event = next(discovered)
            assert isinstance(event, ProgressEvent) and "[UNAVAILABLE]" in event.phase
            assert isinstance(next(discovered), MailContainer)
        assert sum(b" AUTHENTICATE PLAIN" in line for line in server.commands) == (2 if authenticated else 1)
        assert set(archive.iterdir()) == {archive / "config.yaml"}
    finally:
        server.close()


@pytest.mark.parametrize("stage", ["login", "capability", "enable", "starttls", "bye"])
@pytest.mark.parametrize("password,detail", [
    ("simultaneous", b"[ALERT] Too many simultaneous connections."),
    ("[UNAVAILABLE]", b"[UNAVAILABLE] Try later"),
    ("demopass", b"[ALERT] " + b"diagnostic " * 70 + b"too many connections"),
])
def test_connection_limit_classification_precedes_redaction(tmp_path: Path, stage: str,
        password: str, detail: bytes) -> None:
    """ACQUISITION: redaction and bounded diagnostics cannot erase retry evidence at any setup boundary."""
    command = (b"CAPABILITY" if stage in {"capability", "bye"} else b"ENABLE" if stage == "enable"
               else b"STARTTLS" if stage == "starttls" else None)
    server = Utf8Imap(tmp_path, credentials=("demouser", password),
                      login_response=b"NO " + detail if stage == "login" else None,
                      setup_command=command, setup_authenticated=stage == "enable",
                      setup_response=(b"BYE " if stage == "bye" else b"NO ") + detail if command else None,
                      tls="starttls" if stage == "starttls" else "implicit")
    try:
        account = server.start()
        with pytest.raises(ImapServerBusy) as failure:
            with ImapSession(account, password, server.context):
                pytest.fail("temporary refusal was ignored")
        assert len(failure.value.detail) <= 512
        assert password not in "".join(traceback.format_exception(failure.value))
        assert not any(b" LIST " in line or b" EXAMINE " in line for line in server.commands)
    finally:
        server.close()


@pytest.mark.parametrize("command,tls", [(b"CAPABILITY", "implicit"), (b"CAPABILITY", "starttls"), (b"STARTTLS", "starttls")])
@pytest.mark.parametrize("reply", [b"NO", b"BYE"])
def test_credentials_only_capacity_refusal_does_not_retry(tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: bytes, tls: str, reply: bytes) -> None:
    """ACQUISITION: pre-auth NO/BYE refusals return temporary status without retry, credentials or mailbox access."""
    server = Utf8Imap(tmp_path, setup_command=command, setup_authenticated=False,
                      setup_response=reply + b" [UNAVAILABLE] Temporarily unavailable",
                      tls="starttls" if tls == "starttls" else "implicit")
    vault = FixtureKeyring()
    monkeypatch.setattr("mailarchiver.imap_credentials.native_keyring", lambda: vault)
    try:
        account = server.start()
        monkeypatch.setenv("SSL_CERT_FILE", str(server.certificate))
        archive = tmp_path / "config-only"
        save_archive_config(archive, load_archive_config(archive).model_copy(update={"imap_sources": [account]}))
        save_password(account, "demopass")
        assert imap_main(["--archive", str(archive), "--check-credentials"]) == 75
        assert "[UNAVAILABLE]" in capsys.readouterr().err
        expected = [b"CAPABILITY"] if command == b"CAPABILITY" else [b"CAPABILITY", b"STARTTLS"]
        assert [line.split()[1] for line in server.commands] == expected
        assert password_for(account) == "demopass"
        assert set(archive.iterdir()) == {archive / "config.yaml"}
    finally:
        server.close()


@pytest.mark.parametrize("skip", [0, 1], ids=["discovery", "download"])
@pytest.mark.parametrize("imap_server", ["interruptible"], indirect=True)
def test_connection_backoff_completes_import_without_manual_resume(imap_archive: Path,
        imap_inbox: DisposableImap, skip: int) -> None:
    """ACQUISITION: discovery and download login refusals retry without failing the real ingest."""
    assert isinstance(imap_inbox, InterruptibleImap) and imap_inbox.account is not None
    imap_inbox.fault = ImapFault(command=b" AUTHENTICATE PLAIN", mode="reject", skip=skip)
    args = parser().parse_args(["--archive", str(imap_archive), "--no-scan", "--workers", "1"])
    assert synchronize(args) == 0
    status = read_ingest_history(imap_archive).statuses[0]
    assert status.state == "completed" and status.failure_detail is None
    assert password_for(imap_inbox.account) == "demopass"
    assert totals(imap_archive).archived == 3
    assert_imap_searchable(imap_archive)


@pytest.mark.parametrize("valid", [False, True])
def test_credentials_only_cli_leaves_config_only_directory_unchanged(tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], valid: bool) -> None:
    """ACQUISITION: authentication-only CLI needs no archive DB, lease, ingest, or mailbox access."""
    server = Utf8Imap(tmp_path, credentials=("demouser", "demopass"))
    vault = FixtureKeyring()
    monkeypatch.setattr("mailarchiver.imap_credentials.native_keyring", lambda: vault)
    try:
        account = server.start()
        monkeypatch.setenv("SSL_CERT_FILE", str(server.certificate))
        archive = tmp_path / "config-only"
        save_archive_config(archive, load_archive_config(archive).model_copy(update={"imap_sources": [account]}))
        save_password(account, "demopass" if valid else "incorrect")
        before = (archive / "config.yaml").read_bytes()
        assert imap_main(["--archive", str(archive), "--check-credentials"]) == (0 if valid else 1)
        output = capsys.readouterr()
        assert ("authentication succeeded" if valid else "authentication failed") in output.out + output.err
        assert set(archive.iterdir()) == {archive / "config.yaml"}
        assert (archive / "config.yaml").read_bytes() == before
        assert password_for(account) == ("demopass" if valid else "incorrect")
        commands = [line.split()[1] for line in server.commands]
        assert commands.count(b"AUTHENTICATE") == 1 and commands.count(b"CAPABILITY") == 1
        assert set(commands) <= {b"CAPABILITY", b"AUTHENTICATE", b"LOGOUT"}
    finally:
        server.close()


def test_gui_replacement_validates_before_saving_without_ingest(tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """ACQUISITION: desktop replacement preserves an old vault item until real TLS login succeeds."""
    server = Utf8Imap(tmp_path, credentials=("demouser", "demopass"))
    vault = FixtureKeyring()
    monkeypatch.setattr("mailarchiver.imap_credentials.native_keyring", lambda: vault)
    try:
        account = server.start()
        monkeypatch.setenv("SSL_CERT_FILE", str(server.certificate))
        archive = tmp_path / "config-only"
        save_archive_config(archive, load_archive_config(archive).model_copy(update={"imap_sources": [account]}))
        save_password(account, "old-password")
        controls = IngestWindowApi(archive)
        with pytest.raises(ImapAuthenticationError):
            controls.update_imap_password(account.id, "incorrect")
        assert password_for(account) == "old-password"
        assert controls.update_imap_password(account.id, "demopass")
        assert password_for(account) == "demopass"
        assert set(archive.iterdir()) == {archive / "config.yaml"}
        assert not any(b" LIST " in command or b" EXAMINE " in command for command in server.commands)
    finally:
        server.close()


@pytest.mark.parametrize("valid_password", [False, True])
def test_real_tls_plain_authentication_accepts_unicode_and_rejects_bad_password(
    tmp_path: Path, valid_password: bool,
) -> None:
    """ACQUISITION: a PLAIN-only TLS server must accept UTF-8 credentials without LOGIN."""
    username, password = "démouser", "fixture-pässword"
    server = Utf8Imap(tmp_path, credentials=(username, password))
    try:
        account = server.start().model_copy(update={"username": username})
        if valid_password:
            with ImapSession(account, password, server.context) as session:
                assert [mailbox.name for mailbox in session.mailboxes()] == list(server.names)
        else:
            with pytest.raises(ValueError, match="IMAP authentication failed") as failure:
                with ImapSession(account, "incorrect", server.context):
                    pytest.fail("invalid credentials were accepted")
            assert password not in str(failure.value)
        assert any(b" AUTHENTICATE PLAIN" in command for command in server.commands)
        assert not any(b" LOGIN " in command for command in server.commands)
    finally:
        server.close()


@pytest.mark.parametrize("status", [-25293, -25308, None])
def test_credential_errors_keep_native_status_without_private_exception_text(status: int | None,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Native-vault boundary errors must distinguish status codes without exposing backend data."""
    marker = "synthetic-private-backend-value"

    class DeniedVault(FixtureKeyring):
        def get_password(self, service: str, username: str) -> str | None:
            if status is not None:
                raise KeyringError(marker) from OSError(status, marker)
            raise KeyringError(marker)

    monkeypatch.setattr("mailarchiver.imap_credentials.native_keyring", lambda: DeniedVault())
    with pytest.raises(RuntimeError) as failure:
        password_for(ImapAccount(server="fixture.example.test", username="fixture"))
    if status is not None:
        assert f"native status {status}" in str(failure.value)
    assert marker not in str(failure.value)
    assert failure.value.__context__ is None and failure.value.__cause__ is None


@pytest.mark.skipif(os.name == "nt", reason="Synthetic executable credential boundary uses a POSIX shebang")
@pytest.mark.parametrize("response", ["valid", "malformed", "denied"])
def test_development_credential_pipe_preserves_password_and_sanitizes_failures(tmp_path: Path, response: str) -> None:
    """Only the vault reader boundary is substituted; real child pipes must not leak error inputs."""
    marker = "synthetic-private-password-λ\n"
    output = json.dumps({PASSWORD_KEY: marker if response == "valid" else [marker]})
    error = f"KeyringError: {marker!r} (-25293, 'Access denied')" if response == "denied" else ""
    reader = tmp_path / "native-reader"
    reader.write_text(f"#!{sys.executable}\n"
        "# Synthetic OS-credential reader, used only inside this disposable test.\n"
        "# Emit recorded native-boundary responses without opening any vault.\n"
        "# The production reader still launches and captures a real child process.\n"
        "# Valid output checks Unicode/newline fidelity across JSON and pipes.\n"
        "# Invalid and denied output exercise secret-safe error handling.\n"
        f"import sys\nsys.stdout.write({output!r})\nsys.stderr.write({error!r})\n"
        f"sys.exit({1 if response == 'denied' else 0})\n", encoding="utf-8")
    reader.chmod(0o700)
    account = ImapAccount(server="fixture.example.test", username="fixture")
    if response == "valid":
        assert _development_password(str(reader), account) == marker
    else:
        with pytest.raises(RuntimeError) as failure:
            _development_password(str(reader), account)
        assert "synthetic-private-password" not in str(failure.value)
        assert failure.value.__context__ is None and failure.value.__cause__ is None

ROOT = Path(__file__).resolve().parents[1]
# Keep the actual CLI entry point and replace only native keychain access in children.
# The well-known disposable-server password is synthetic and has no external authority.
CLI_DRIVER = """
from pathlib import Path
import sys
from tests.imap_fixture import FixtureKeyring
from mailarchiver import imap_credentials
from mailarchiver.imap_cli import main
from mailarchiver.archive_config import load_archive_config
vault = FixtureKeyring()
if '--archive' in sys.argv:
    archive = Path(sys.argv[sys.argv.index('--archive') + 1])
    for account in load_archive_config(archive).imap_sources:
        vault.set_password(account.credential_ref, account.username, 'demopass')
imap_credentials.native_keyring = lambda: vault
raise SystemExit(main())
"""


def test_ect_imap_credentials_only_uses_dispatcher_without_archive_writes(tmp_path: Path) -> None:
    """ACQUISITION: the public ect imap dispatcher performs only a stored-credential login."""
    server = Utf8Imap(tmp_path, credentials=("demouser", "demopass"))
    try:
        account = server.start()
        archive = tmp_path / "config-only"
        save_archive_config(archive, load_archive_config(archive).model_copy(update={"imap_sources": [account]}))
        before = (archive / "config.yaml").read_bytes()
        driver = CLI_DRIVER.replace("from mailarchiver.imap_cli import main", "from mailarchiver.cli import main")
        environment = os.environ.copy()
        environment[SSL_CERT_FILE_ENV] = str(server.certificate)
        result = subprocess.run([sys.executable, "-c", driver, "imap", "--archive", str(archive), "--check-credentials"],
                                env=environment, capture_output=True, text=True, check=False, timeout=30)
        assert result.returncode == 0 and "authentication succeeded" in result.stdout, result.stderr
        assert set(archive.iterdir()) == {archive / "config.yaml"}
        assert (archive / "config.yaml").read_bytes() == before
        commands = [line.split()[1] for line in server.commands]
        assert commands.count(b"AUTHENTICATE") == 1 and set(commands) <= {b"CAPABILITY", b"AUTHENTICATE", b"LOGOUT"}
    finally:
        server.close()


@pytest.fixture(autouse=True)
def imap_server_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real loopback server's greeting independent of host reverse DNS."""
    monkeypatch.setenv("FQDN", "localhost")


@pytest.fixture
def imap_server(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[DisposableImap]:
    """Pytest owns the isolated TLS listener, including cleanup after setup/assertion failures."""
    tls = getattr(request, "param", "implicit")
    assert tls in {"implicit", "starttls", "interruptible"}
    server = (InterruptibleImap(tmp_path) if tls == "interruptible" else
              DisposableImap(tmp_path, "implicit" if tls == "implicit" else "starttls"))
    try:
        server.start()
        yield server
    finally:
        server.close()


@pytest.mark.parametrize("capability", ["UTF8=ACCEPT", "UTF8=ONLY"])
def test_utf8_mailbox_negotiation_and_read_only_wire_names(tmp_path: Path, capability: str) -> None:
    """RFC 6855 folders round-trip UTF-8, literal ampersands and quotes over verified TLS."""
    server = Utf8Imap(tmp_path, capability)
    try:
        account = server.start()
        with ImapSession(account, "demopass", server.context) as session:
            folders = session.mailboxes()
            assert [folder.name for folder in folders] == list(server.names)
            for folder in folders:
                selected = session.select(folder)
                assert (selected.uid_validity, selected.uid_next) == (42, 2)
                assert session.uids(1, 1) == [1]
        commands = [line.split(b" ", 1)[1].rstrip(b"\r\n") for line in server.commands]
        assert commands.index(b"ENABLE UTF8=ACCEPT") < commands.index(b'LIST "" "*"')
        assert [command for command in commands if command.startswith(b"EXAMINE ")] == [
            b'EXAMINE "\xe6\x97\x85\xe8\xa1\x8c"', b'EXAMINE "literal &AMk- &-"',
            b'EXAMINE "Quote \\" and \\\\ box"']
        assert not any(command.startswith(b"SELECT ") for command in commands)
    finally:
        server.close()


@pytest.mark.parametrize("response", ["NO", "MISSING"])
def test_utf8_negotiation_failure_never_uses_utf8_folders(tmp_path: Path, response: str) -> None:
    """Advertised UTF-8 must actually be enabled before discovering or selecting folders."""
    server = Utf8Imap(tmp_path, enable=response)
    try:
        account = server.start()
        with pytest.raises(RuntimeError, match="UTF8=ACCEPT"):
            with ImapSession(account, "demopass", server.context):
                pytest.fail("Unacknowledged UTF-8 negotiation must fail")
        assert not any(b" LIST " in line or b" EXAMINE " in line for line in server.commands)
    finally:
        server.close()


def test_legacy_unicode_folder_read_only_selection(imap_server: DisposableImap) -> None:
    """Servers without UTF8=ACCEPT retain their modified UTF-7 LIST/EXAMINE spelling."""
    wire_name = '"&AMk-tudes &- More"'
    raw = b"Subject: legacy\r\n\r\nbody\r\n"
    with imap_server.connect() as client:
        assert client.create(wire_name)[0] == "OK"
        assert client.append(wire_name, "(\\Flagged)", None, raw)[0] == "OK"
    assert imap_server.account is not None
    with ImapSession(imap_server.account, "demopass", imap_server.context) as session:
        folder = next(folder for folder in session.mailboxes() if folder.name == "Études & More")
        assert not session.utf8 and folder.wire_name == wire_name[1:-1]
        selected = session.select(folder)
        uids = session.uids(1, selected.uid_next - 1)
        assert selected.uid_validity and len(uids) == 1
        message, = session.metadata(selected, uids)
        assert session.body(message) == raw


@pytest.fixture
def imap_messages() -> tuple[bytes, ...]:
    """Three authored messages cover attachments, duplicate IDs and malformed MIME/encoding."""
    return (
        b"From: other@example.org\r\nMessage-ID: <same@example.org>\r\n"
        b"Subject: attachments\x1b[31m\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=x\r\n\r\n"
        b"--x\r\nContent-Type: text/plain\r\n\r\nbody\r\n--x\r\nContent-Type: text/plain\r\n"
        b"Content-Disposition: attachment; filename=example.txt\r\n\r\nneedle-attachment\r\n"
        b"--x\r\nContent-Type: application/octet-stream\r\nContent-Transfer-Encoding: base64\r\n"
        b"Content-Disposition: attachment; filename=blob.bin\r\n\r\nAP8NCmJpbmFyeQ==\r\n--x--\r\n",
        b"From: other@example.org\r\nMessage-ID: <same@example.org>\r\nSubject: distinct bytes\r\n\r\nFrom preservation\r\n",
        b"From: other@example.org\r\nSubject: malformed MIME\r\nContent-Type: multipart/mixed; boundary=missing\r\n\r\n\xff retained\r\n",
    )


@pytest.fixture
def imap_inbox(imap_server: DisposableImap, imap_messages: tuple[bytes, ...]) -> DisposableImap:
    """Populate exactly three synthetic INBOX messages, retaining mixed seen/unseen flags."""
    with imap_server.connect() as client:
        for index, raw in enumerate(imap_messages):
            flags = r"(\Seen)" if index == 0 else None
            assert client.append("INBOX", flags, '"01-Oct-2026 10:00:00 +0000"', raw)[0] == "OK"
    return imap_server


@pytest.fixture
def imap_archive(tmp_path: Path, imap_inbox: DisposableImap, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Configure a synthetic archive with real TLS trust and an isolated native-secrets boundary."""
    account = imap_inbox.account
    assert account is not None
    keyring = FixtureKeyring()
    monkeypatch.setattr("mailarchiver.imap_credentials.native_keyring", lambda: keyring)
    monkeypatch.setenv("SSL_CERT_FILE", str(imap_inbox.certificate))
    archive = tmp_path / "collection.mailarchive"
    create_empty_archive(archive)
    config = load_archive_config(archive)
    config.owner = OwnerRules(include=["demouser"])
    config.imap_sources = [account]
    save_archive_config(archive, config)
    save_password(account, "demopass")
    return archive


def assert_imap_searchable(archive: Path) -> None:
    """Acquisition/search requirements: archival success must also finish deferred indexing."""
    assert len(search_page(archive, "").results) == 3
    for query, attachments, subject in (
        ("body", False, "attachments"), ("preservation", False, "distinct bytes"),
        ("needle", True, "attachments"), ("subject:malformed", False, "malformed MIME"),
    ):
        result = search_page(archive, query, search_attachments=attachments)
        assert result.error is None and len(result.results) == 1, (query, result)
        assert result.results[0].subject.startswith(subject)
    assert not search_page(archive, "needle").results
    with closing(sqlite3.connect(archive / "processing.sqlite3")) as database:
        assert database.execute("SELECT count(*) FROM jobs WHERE status!='completed'").fetchone() == (0,)
        assert database.execute("SELECT count(*) FROM invocations WHERE kind='text-index' AND status='completed'").fetchone()[0] > 0


@pytest.mark.parametrize("connections", [8, 3, 15])
def test_imap_connection_cap_preserves_processing_workers(imap_archive: Path, imap_inbox: DisposableImap,
        connections: int) -> None:
    """ACQUISITION: separate connection budgets constrain real TLS sessions, retaining sixteen workers."""
    assert imap_inbox.account is not None
    with imap_inbox.connect() as client:
        for number in range(28):
            client.append("INBOX", None, None, f"Subject: limit {number}\r\n\r\nsynthetic {number}\r\n".encode())
    imap_inbox.peak_connections = 0
    imap_inbox.connection_target = connections
    imap_inbox.pause_after_bodies(1)
    request = IngestRequest(archive=imap_archive, roots=[imap_locator(imap_archive, imap_inbox.account, 16)],
                            workers=16, imap_connections=connections, scan_policy="not-scanned")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_ingest, request, terminal=False)
        try:
            assert imap_inbox.connections_ready.wait(15), f"{connections} acquisition sessions were not admitted"
            assert not future.done()
        finally:
            imap_inbox.release_bodies()
        future.result(timeout=30)
    assert imap_inbox.peak_connections == connections
    assert read_ingest_history(imap_archive).statuses[0].configured_workers == 16
    assert resume_request(imap_archive, False, True).workers == 16
    assert resume_request(imap_archive, False, True).imap_connections is None  # Reload personal preferences on continuation.
    assert totals(imap_archive).archived == 31
    assert not verify_archive(imap_archive)


def test_ect_import_uses_saved_imap_sources(imap_archive: Path, imap_inbox: DisposableImap) -> None:
    """ACQUISITION: the unified CLI runs real TLS IMAP and resumes without duplicate messages."""
    from mailarchiver.cli import main as ect_main  # pylint: disable=import-outside-toplevel
    arguments = ["import", "--archive", str(imap_archive), "--imap", "--no-scan", "--workers", "1"]
    with imap_inbox.connect() as client:
        client.select("INBOX", readonly=True)
        flags = client.uid("FETCH", "1:*", "(UID FLAGS RFC822.SIZE)")[1]
    assert ect_main(arguments) == 0
    assert_imap_searchable(imap_archive)
    before = totals(imap_archive)
    assert ect_main(arguments) == 0
    assert totals(imap_archive) == before
    with imap_inbox.connect() as client:
        client.select("INBOX", readonly=True)
        assert client.uid("FETCH", "1:*", "(UID FLAGS RFC822.SIZE)")[1] == flags


@pytest.mark.parametrize("imap_server", ["implicit", "starttls"], indirect=True)
def test_cli_real_server_normalizes_crlf_skips_spam_and_resumes(imap_archive: Path,
        imap_inbox: DisposableImap, imap_messages: tuple[bytes, ...], capsys: pytest.CaptureFixture[str]) -> None:
    """IMAP requirements: normalize CRLF once, retain lone CR/wire evidence, and stay read-only."""
    server, archive, messages = imap_inbox, imap_archive, imap_messages
    tls = server.tls
    normalized = [raw.replace(b"\r\n", b"\n") for raw in messages]
    with server.connect() as client:
        assert b"Server ready localhost" in client.welcome
        assert client.create("Projects")[0] == "OK"
        assert client.create("MixedSpAmFolder")[0] == "OK"
        assert client.append("Projects", None, None, messages[0])[0] == "OK"
        assert client.append("MixedSpAmFolder", None, None, b"Subject: excluded spam\r\n\r\nspam\r\n")[0] == "OK"
        client.select("INBOX", readonly=True)
        before = client.uid("FETCH", "1:*", "(UID FLAGS RFC822.SIZE)")[1]
    assert "demopass" not in (archive / "config.yaml").read_text()
    arguments = ["--archive", str(archive), "--show-messages"]
    if tls == "starttls":
        arguments.append("--no-scan")
    args = parser().parse_args(arguments)
    assert args.workers is None  # An omitted CLI value delegates to application preferences.
    assert synchronize(args) == 0
    summary = totals(archive)
    assert summary.archived == 3
    assert summary.duplicates == 1
    assert summary.errors == 0
    assert_imap_searchable(archive)
    assert not list((archive / "objects").iterdir())
    with closing(sqlite3.connect(archive / "archive.sqlite3")) as database:
        hashes = {row[0] for row in database.execute("SELECT sha256 FROM messages")}
        assert hashes == {hashlib.sha256(raw).hexdigest() for raw in normalized}
        for digest, filename, offset, length in database.execute(
                "SELECT m.sha256,g.filename,l.byte_offset,l.byte_length FROM messages m "
                "JOIN locations l USING(message_pk) JOIN mbox_generations g USING(generation_pk)"):
            raw = read_verified_location(archive / "data/mbox" / filename,
                MboxLocation(byte_offset=offset, byte_length=length), digest)
            assert raw in normalized
            assert b"\r\n" not in raw
            if b"filename=blob.bin" in raw:
                attachment = next(part for part in BytesParser().parsebytes(raw).walk() if part.get_filename() == "blob.bin")
                assert attachment.get_payload(decode=True) == b"\x00\xff\r\nbinary"
        assert database.execute("SELECT count(*) FROM imap_seen").fetchone() == (4,)
        unscanned = database.execute("SELECT count(*) FROM metadata_defects WHERE field='antivirus' "
                                     "AND detail LIKE 'not-scanned:%'").fetchone()[0]
        assert unscanned == (3 if tls == "starttls" else 0)
        assert database.execute("SELECT sum(archived),sum(duplicates) FROM imap_hourly").fetchone() == (3, 1)
        assert database.execute("SELECT count(*) FROM source_files WHERE source_path LIKE '%SpAm%'").fetchone() == (0,)
    assert synchronize(args) == 0
    assert totals(archive) == summary
    assert_imap_searchable(archive)
    with closing(sqlite3.connect(archive / "archive.sqlite3")) as database:
        evidence = [ImapMessageMetadata.model_validate_json(row[0]).download
                    for row in database.execute("SELECT labels_json FROM imap_seen")]
        assert len(evidence) == 4
        for original in messages:
            assert any(item is not None and item.source_sha256 == hashlib.sha256(original).hexdigest()
                       and item.source_byte_length == len(original) and item.crlf_pairs == original.count(b"\r\n")
                       for item in evidence)
    output = capsys.readouterr().err
    assert "completed: newly archived=0 duplicate downloads=0" in output
    assert "archived: demouser: INBOX:" in output or "archived: demouser: Projects:" in output
    assert "?partition=" not in output
    assert "\x1b" not in output
    with server.connect() as client:
        client.select("INBOX", readonly=True)
        assert client.uid("FETCH", "1:*", "(UID FLAGS RFC822.SIZE)")[1] == before
    with server.connect() as client:
        assert client.delete("Projects")[0] == "OK"
        assert client.create("Projects")[0] == "OK"
        assert client.append("Projects", None, None, messages[0])[0] == "OK"
    assert synchronize(args) == 0
    assert totals(archive).archived == 3
    assert totals(archive).duplicates == 2
    assert not list((archive / "objects").iterdir())


def test_gui_background_ingests_three_inbox_messages_and_indexes_them(imap_archive: Path,
        imap_inbox: DisposableImap, imap_messages: tuple[bytes, ...], tmp_path: Path) -> None:
    """Desktop IMAP requirement: the actual background entry point archives/indexes and reruns read-only."""
    account = imap_inbox.account
    assert account is not None
    with imap_inbox.connect() as client:
        assert client.select("INBOX", readonly=True)[1] == [b"3"]
        before = client.uid("FETCH", "1:*", "(UID FLAGS RFC822.SIZE BODY.PEEK[])")[1]
    app, api = open_headless(imap_archive, tmp_path / "preferences.json")
    owners_file = tmp_path / "accepted-owners.txt"
    owners_file.write_text("demouser\n", encoding="utf-8")
    request = IngestRequest(archive=imap_archive, roots=[imap_locator(imap_archive, account, 8)],
                            workers=8, index_attachments=True, owner_names_file=owners_file)
    release = Event()
    occupied = Barrier(9)

    def block() -> None:
        occupied.wait(timeout=5)
        assert release.wait(10), "fixture release deadline expired"

    try:
        # Suspend before preflight has saved processing settings, then resume the
        # exact accepted request. This also proves suspension does not quit.
        for _ in range(8):
            assert app._submit_task("pause fixture", block) is not None
        occupied.wait(timeout=5)
        assert app.start_import(api, [], processing_request=request)
        assert api.document is not None and api.document.ingest_job is not None
        job = api.document.ingest_job
        controls = IngestWindowApi(imap_archive, application=app, document=api.document)
        assert controls.suspend_import()
        assert controls.history()["suspending"]
        assert not controls.resume_import()
        release.set()
        assert job.finished.wait(20)
        assert api.document.ingest_job is None
        assert controls.history()["can_resume"]
        with WriterLease.acquire(imap_archive, api.document.descriptor.identity, "pause fixture", "paused", "test"):
            pass
        owners_file.unlink()
        config = load_archive_config(imap_archive)
        config.owner = OwnerRules(include=["later-owner"])
        save_archive_config(imap_archive, config)
        request = resume_request(imap_archive, True, True)
        assert request.owner_names_file is None and request.owner_rules == OwnerRules(include=["demouser"])
        assert request.index_attachments and request.workers == app.worker_settings().effective_count
        app.shutdown()
        api.close()
        app, api = open_headless(imap_archive, tmp_path / "preferences.json")
        controls = IngestWindowApi(imap_archive, application=app, document=api.document)
        assert controls.history()["can_resume"]
        assert controls.resume_import()
        wait_for_import(api)
        for _attempt in range(2):
            assert app.start_import(api, [], processing_request=request)
            wait_for_import(api)
            assert not [notice for notice in app.notices() if notice.severity == "error"], app.notices()
            status = read_ingest_history(imap_archive).statuses[0]
            assert status.state == "completed", status.failure_detail
            assert totals(imap_archive).archived == 3 and totals(imap_archive).duplicates == 0
            assert not unfinished_work(imap_archive).incomplete
            assert_imap_searchable(imap_archive)
            results = SearchPage.model_validate(api.search("preservation"))
            assert len(results.results) == 1 and results.results[0].subject == "distinct bytes"
        with closing(sqlite3.connect(imap_archive / "archive.sqlite3")) as database:
            assert {row[0] for row in database.execute("SELECT sha256 FROM messages")} == {
                hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() for raw in imap_messages}
            assert database.execute("SELECT count(*) FROM imap_seen").fetchone() == (3,)
        assert not verify_archive(imap_archive)
        with imap_inbox.connect() as client:
            assert client.select("INBOX", readonly=True)[1] == [b"3"]
            assert client.uid("FETCH", "1:*", "(UID FLAGS RFC822.SIZE BODY.PEEK[])")[1] == before
    finally:
        release.set()
        app.shutdown()
        api.close()


@pytest.mark.parametrize("imap_server", ["interruptible"], indirect=True)
def test_standalone_cli_reports_network_pause_as_temporary(imap_archive: Path,
        imap_inbox: DisposableImap, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI pauses retain checkpoints and return the temporary-failure exit code without a traceback."""
    assert isinstance(imap_inbox, InterruptibleImap)
    monkeypatch.setattr("mailarchiver.imap_protocol.IMAP_TIMEOUT", 1)
    imap_inbox.fault = ImapFault(command=b"BODY.PEEK[]", mode="timeout", skip=1)
    args = parser().parse_args(["--archive", str(imap_archive), "--no-scan", "--workers", "1"])
    assert synchronize(args) == 75
    assert read_ingest_history(imap_archive).statuses[0].state == "paused"
    output = capsys.readouterr().err
    assert "paused" in output and "failed" not in output and "Traceback" not in output
    imap_inbox.fault = None
    assert synchronize(args) == 0
    assert totals(imap_archive).archived == 3 and totals(imap_archive).duplicates == 0


@pytest.mark.parametrize("imap_server", ["interruptible"], indirect=True)
@pytest.mark.parametrize("response", [b"invalid IMAP response\r\n", b"invalid IMAP response\n",
                                      b"socket error: invalid IMAP response\r\n"])
def test_invalid_imap_response_remains_a_failure(imap_archive: Path, imap_inbox: DisposableImap,
                                                response: bytes) -> None:
    """A malformed response is an acquisition defect, even though imaplib raises abort for it."""
    assert isinstance(imap_inbox, InterruptibleImap)
    imap_inbox.fault = ImapFault(command=b"BODY.PEEK[]", mode="malformed", skip=1, response=response)
    args = parser().parse_args(["--archive", str(imap_archive), "--no-scan", "--workers", "1"])
    with pytest.raises(imaplib.IMAP4.abort, match="unexpected response|unterminated line"):
        synchronize(args)
    status = read_ingest_history(imap_archive).statuses[0]
    assert status.state == "failed" and status.failure_detail and "invalid IMAP response" in status.failure_detail
    imap_inbox.fault = None
    assert synchronize(args) == 0
    assert totals(imap_archive).archived == 3 and totals(imap_archive).duplicates == 0


@pytest.mark.parametrize("wrapped", [False, True], ids=["direct", "imaplib-wrapped"])
def test_host_down_is_a_resumable_transport_interruption(wrapped: bool) -> None:
    """ACQUISITION: host-down socket failures pause instead of recording a failed ingest."""
    host_down = getattr(errno, "EHOSTDOWN", None)
    if host_down is None:
        pytest.skip("This platform does not expose EHOSTDOWN")
    session = ImapSession(ImapAccount(server="fixture.example.test", username="fixture"), "unused")
    try:
        try:
            raise OSError(host_down, "Host is down")
        except OSError:
            if wrapped:
                raise imaplib.IMAP4.abort("socket error: Host is down")
            raise
    except (OSError, imaplib.IMAP4.abort) as error:
        assert connection_interrupted(error)
        with pytest.raises(ImapPaused, match="IMAP connection was interrupted.*use Resume"):
            session.__exit__(type(error), error, error.__traceback__)


@pytest.mark.parametrize("error", [OSError("Host is down"), OSError(errno.EACCES, "Host is down")])
def test_host_down_handling_requires_a_transport_errno(error: OSError) -> None:
    """ACQUISITION: host-down-looking text alone must not hide genuine failures."""
    assert not connection_interrupted(error)


def test_imaplib_wrapped_send_timeout_retains_timeout_presentation() -> None:
    """Stdlib's command-send wrapper must retain the same Timeout reason as socket reads."""
    session = ImapSession(ImapAccount(server="fixture.example.test", username="fixture"), "unused")
    try:
        try:
            raise TimeoutError("The write operation timed out")
        except TimeoutError as cause:
            raise imaplib.IMAP4.abort("socket error: The write operation timed out") from cause
    except imaplib.IMAP4.abort as error:
        with pytest.raises(ImapPaused, match="IMAP connection timed out"):
            session.__exit__(type(error), error, error.__traceback__)


@pytest.mark.parametrize("public", [False, True], ids=["engine-cli", "ect-import"])
def test_shared_cli_login_rejection_has_no_traceback(imap_archive: Path, imap_inbox: DisposableImap, public: bool) -> None:
    """ACQUISITION: the general ingest CLI also reports real login rejection without a traceback."""
    assert imap_inbox.account is not None
    module = "mailarchiver.cli" if public else "mailarchiver.__main__"
    driver = CLI_DRIVER.replace("from mailarchiver.imap_cli import main", f"from {module} import main")
    driver = driver.replace("'demopass'", "'incorrect'")
    arguments = (["import", "--archive", str(imap_archive), "--imap", "--no-scan", "--workers", "1"] if public else
                 ["--archive", str(imap_archive), "ingest", "--no-scan", "--workers", "1",
                  imap_locator(imap_archive, imap_inbox.account, 1)])
    result = subprocess.run([sys.executable, "-c", driver, *arguments],
        capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 1
    assert "IMAP authentication failed" in result.stderr and "Traceback" not in result.stderr
    assert "--set-password" in result.stderr
    status = read_ingest_history(imap_archive).statuses[0]
    assert status.state == "failed" and status.failure_detail and "Traceback" not in status.failure_detail


@pytest.mark.parametrize("shortcut", [False, True], ids=["ect-import-command", "ect-import-shortcut"])
@pytest.mark.parametrize("paused", [False, True], ids=["rejected", "interrupted"])
def test_shared_cli_login_reason_survives_long_account_names(tmp_path: Path, shortcut: bool, paused: bool) -> None:
    """ACQUISITION: public imports retain rejection diagnostics and temporary-pause status/guidance."""
    username = "long.synthetic.username@example.test"
    reason = b"[AUTHENTICATIONFAILED] Explicit synthetic policy reason"
    server = Utf8Imap(tmp_path, credentials=(username, "demopass"),
                      login_response=None if paused else b"NO " + reason + b" demopass",
                      setup_command=b"CAPABILITY" if paused else None, setup_authenticated=False,
                      setup_response=b"BYE Server shutting down" if paused else None)
    try:
        account = server.start().model_copy(update={"username": username})
        archive = tmp_path / "archive"
        create_empty_archive(archive)
        config = load_archive_config(archive).model_copy(update={"imap_sources": [account], "owner": OwnerRules(include=[username])})
        save_archive_config(archive, config)
        driver = CLI_DRIVER.replace("from mailarchiver.imap_cli import main", "from mailarchiver.cli import main")
        if shortcut:
            driver = driver.replace("raise SystemExit(main())", "sys.argv[0] = 'ect-import'\nraise SystemExit(main())")
        arguments = ([] if shortcut else ["import"]) + ["--archive", str(archive), "--imap", "--no-scan", "--workers", "1"]
        result = subprocess.run([sys.executable, "-c", driver, *arguments], env=os.environ | {SSL_CERT_FILE_ENV: str(server.certificate)},
                                capture_output=True, text=True, check=False, timeout=30)
        assert result.returncode == (75 if paused else 1) and "Traceback" not in result.stderr
        if paused:
            assert "use Resume when connected" in result.stderr
            assert read_ingest_history(archive).statuses[0].state == "paused"
        else:
            assert reason.decode() in result.stderr and "--set-password" in result.stderr
            assert "[redacted]" in result.stderr
        assert "demopass" not in result.stderr
        assert not any(b" LIST " in line or b" EXAMINE " in line for line in server.commands)
    finally:
        server.close()


def test_rejected_credentials_remain_a_failure(imap_archive: Path, imap_inbox: DisposableImap) -> None:
    """An authentication rejection must request corrected credentials rather than network resumption."""
    assert imap_inbox.account is not None
    save_password(imap_inbox.account, "incorrect-synthetic-password")
    args = parser().parse_args(["--archive", str(imap_archive), "--no-scan", "--workers", "1"])
    with pytest.raises(ValueError, match="authentication failed"):
        synchronize(args)
    status = read_ingest_history(imap_archive).statuses[0]
    assert status.state == "failed" and status.failure_detail and "authentication failed" in status.failure_detail
    assert "Traceback" not in status.failure_detail and "Message prefix" not in status.failure_detail
    # Even first discovery rejection retains the exact import settings for password repair.
    request = resume_request(imap_archive, True, True)
    assert request.roots and request.scan_policy == "not-scanned" and request.workers == 1
    save_password(imap_inbox.account, "demopass")
    from mailarchiver.__main__ import run_ingest  # pylint: disable=import-outside-toplevel
    run_ingest(request, terminal=False)
    assert totals(imap_archive).archived == 3
    assert_imap_searchable(imap_archive)


@pytest.mark.parametrize("imap_server", ["interruptible"], indirect=True)
@pytest.mark.parametrize("gui", [False, True], ids=["cli", "gui"])
def test_network_pause_does_not_hide_finalization_failure(imap_archive: Path, imap_inbox: DisposableImap,
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gui: bool) -> None:
    """A real destination-write failure after a timeout must override the expected network pause."""
    assert isinstance(imap_inbox, InterruptibleImap) and imap_inbox.account is not None
    monkeypatch.setattr("mailarchiver.imap_protocol.IMAP_TIMEOUT", 1)
    imap_inbox.fault = ImapFault(command=b" LIST ", mode="timeout")
    # This is a fresh synthetic archive: obstruct the real final metadata replacement.
    obstruction = imap_archive / OWNER_NAMES_DETECTED
    obstruction.mkdir()
    expected_error = PermissionError if sys.platform == "win32" else IsADirectoryError
    args = parser().parse_args(["--archive", str(imap_archive), "--no-scan", "--workers", "1"])
    app, api = open_headless(imap_archive, tmp_path / "preferences.json", workers_override=1)
    try:
        if gui:
            request = IngestRequest(archive=imap_archive,
                roots=[imap_locator(imap_archive, imap_inbox.account, 1)], workers=1, scan_policy="not-scanned")
            assert app.start_import(api, [], processing_request=request)
            wait_for_import(api)
            assert any(notice.severity == "error" and expected_error.__name__ in notice.message
                       and OWNER_NAMES_DETECTED in notice.message
                       for notice in app.notices()), app.notices()
            assert not any("Import paused" in notice.message for notice in app.notices())
        else:
            with pytest.raises(expected_error) as failure:
                synchronize(args)
            assert failure.value.filename2 == str(obstruction)
        status = read_ingest_history(imap_archive).statuses[0]
        assert status.state == "failed" and status.failure_detail and OWNER_NAMES_DETECTED in status.failure_detail
        assert api.document is not None and api.document.ingest_job is None
        with WriterLease.acquire(imap_archive, api.document.descriptor.identity, "failure fixture", "failed", "test"):
            pass
        obstruction.rmdir()
        imap_inbox.fault = None
        assert synchronize(args) == 0
        assert totals(imap_archive).archived == 3 and totals(imap_archive).duplicates == 0
        assert not verify_archive(imap_archive)
    finally:
        imap_inbox.fault = None
        app.shutdown()
        api.close()


def test_resume_downloads_checkpoint_gaps_before_saved_metadata(imap_archive: Path,
        imap_inbox: DisposableImap) -> None:
    """IMAP resume: missing UIDs take priority without skipping holes or mutable-label refresh."""
    archive, server = imap_archive, imap_inbox
    account = server.account
    assert account is not None
    args = parser().parse_args(["--archive", str(archive), "--no-scan", "--workers", "1"])
    assert synchronize(args) == 0
    # Simulate a missing durable per-message checkpoint between two committed UIDs.
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        prior_uids = [row[0] for row in database.execute("SELECT uid FROM imap_seen ORDER BY uid")]
        assert len(prior_uids) == 3
        database.execute("DELETE FROM imap_seen WHERE uid=?", (prior_uids[1],))
    with server.connect() as client:
        assert client.append("INBOX", None, None,
            b"From: other@example.org\r\nSubject: newly arrived\r\n\r\nnew body\r\n")[0] == "OK"
        client.select("INBOX", readonly=True)
        before = client.uid("FETCH", "1:*", "(UID FLAGS RFC822.SIZE)")[1]
        new_uid = max(int(uid) for uid in client.uid("SEARCH", "ALL")[1][0].split())
    adapter = ImapSourcePlugin()
    containers = [item for item in adapter.discover(SourceSpec(kind="imap", locator=imap_locator(archive, account, 1)))
                  if isinstance(item, MailContainer)]
    assert len(containers) == 1
    events = [item for item in adapter.messages(containers[0], None) if isinstance(item, (MailObject, SourceObservation))]
    assert [(type(item), int(item.cursor.split(":")[-1])) for item in events] == [
        (MailObject, prior_uids[1]), (MailObject, new_uid), (SourceObservation, prior_uids[0]), (SourceObservation, prior_uids[2])]
    assert synchronize(args) == 0
    saved = totals(archive)
    assert saved.archived == 4 and saved.duplicates == 1 and saved.errors == 0
    # Worker repartitioning must not turn a missing-checkpoint priority pass into a cursor skip.
    args.workers = min(3, 2 * (os.cpu_count() or 1))
    assert synchronize(args) == 0 and totals(archive) == saved
    assert len(search_page(archive, "").results) == 4
    with server.connect() as client:
        client.select("INBOX", readonly=True)
        assert client.uid("FETCH", "1:*", "(UID FLAGS RFC822.SIZE)")[1] == before


def test_gmail_metadata_literals_and_modified_utf7() -> None:
    """Gmail labels retain escaped, Unicode and literal mailbox names without headers."""
    assert decode_mailbox(b"&AMk-tudes &- More") == "Études & More"
    tokens = response_tokens([
        (b'1 (UID 4 X-GM-MSGID 18446744073709551615 X-GM-LABELS (\\Inbox "Projects/2026" {5}', b"a b c"), b"))",
    ])
    assert tokens[1].children[3].value == b"18446744073709551615"
    assert [token.value for token in tokens[1].children[5].children] == [b"\\Inbox", b"Projects/2026", b"a b c"]
    mailbox = ImapMailbox(name="Tous les messages", wire_name="Tous les messages", flags=("\\all",), uid_validity=17)
    other = ImapMailbox(name="Inbox", wire_name="INBOX")
    assert selected_mailboxes([other, mailbox], True) == [mailbox]
    with pytest.raises(ValueError, match="All Mail"):
        selected_mailboxes([other], True)
    parsed = message_metadata([
        b'2 (FLAGS (\\Seen))',
        b'1 (UID 4 INTERNALDATE "01-Oct-2026 10:00:00 +0000" X-GM-MSGID 18446744073709551615 '
        b'X-GM-LABELS (\\Inbox "Projects/2026"))',
    ], mailbox, [4], True)
    assert parsed[0].gmail_id == "18446744073709551615"
    assert parsed[0].labels == ("\\Inbox", "Projects/2026")
    assert parsed[0].internal_date == datetime(2026, 10, 1, 10, tzinfo=UTC)


def test_body_literal_allows_legal_fetch_order_and_unsolicited_flags() -> None:
    """UID/size may follow the opaque literal; unsolicited flag data must not change body identity."""
    raw = b"Subject: literal\r\n\r\n\xff\x00\"(body)\r\n"
    suffix = f" UID 7 RFC822.SIZE {len(raw)})".encode()
    data = [b'2 (FLAGS (\\Seen))', (f"1 (BODY[] {{{len(raw)}}}".encode(), raw), suffix]
    assert body_literal(data, 7) == raw
    with pytest.raises(RuntimeError, match="identity"):
        body_literal(data, 8)
    with pytest.raises(RuntimeError, match="incomplete"):
        body_literal([(b"1 (BODY[] {3}", b"abc"), b" UID 7 RFC822.SIZE 4)"], 7)


def test_untrusted_tls_is_rejected_before_login(imap_server: DisposableImap) -> None:
    """The importer must not offer an unverified or plaintext credential path."""
    import ssl  # pylint: disable=import-outside-toplevel
    account = imap_server.account
    assert account is not None
    with pytest.raises(ssl.SSLCertVerificationError):
        with ImapSession(account, "demopass"):
            pytest.fail("an untrusted server certificate was accepted")


def test_gmail_checkpoint_is_account_scoped_refreshes_labels_and_counts_atomically(tmp_path: Path) -> None:
    """Unsigned Gmail IDs survive UID resets; known labels update without download counts."""
    archive = tmp_path / "archive"
    create_empty_archive(archive)
    source = SourceReference(plugin_kind="imap", source_id="account1", native_id="All Mail", display_name="All Mail")
    metadata = ImapMessageMetadata(uid_validity=1, uid=7, gmail_id="18446744073709551615", labels=("Projects/2026",),
        download=ImapDownload(source_sha256=hashlib.sha256(b"fixture").hexdigest(), source_byte_length=7, crlf_pairs=0))
    with closing(sqlite3.connect(archive / "archive.sqlite3")) as database:
        initialize_state(database)
        database.execute("INSERT INTO ingest_runs(run_pk,started_at) VALUES (1,?)", (datetime.now(UTC).isoformat(),))
        database.execute("INSERT INTO source_volumes(identity_json,metadata_json,first_observed_at,last_observed_at) VALUES ('{}','{}','now','now')")
        database.execute("INSERT INTO source_files(source_volume_pk,source_plugin,work_id,source_path,hierarchy_path,metadata_json,path_kind,source_kind) "
                         "VALUES (1,'imap','work','All Mail','All Mail','{}','provider','imap')")
        for index in range(1, 11):
            database.execute("INSERT INTO observations(observation_pk,run_pk,source_file_pk,disposition,detail) VALUES (?,1,1,'archived','fixture')", (index,))
            item = metadata.model_copy(update={"uid": index, "gmail_id": str(18446744073709551615 - index)})
            record_download(database, MailObject(work_id="work", source=source, cursor=f"1:{index}", raw=b"fixture",
                            provider_metadata_json=item.model_dump_json()), index, "archived",
                            now=datetime(2026, 10, 16, 10, index, tzinfo=UTC))
            database.commit()
            assert totals(archive).archived == index
        assert database.execute("SELECT hour_utc,archived FROM imap_hourly").fetchall() == [
            ("2026-10-16T10:00:00+00:00", 10)]
        item = metadata.model_copy(update={"uid": 10, "gmail_id": str(18446744073709551615 - 10)})
        assert known_observation(database, source, item) == 10
        assert known_observation(database, source.model_copy(update={"source_id": "account2"}), item) is None
        changed = item.model_copy(update={"uid_validity": 2, "uid": 400, "labels": ("New/Label",), "download": None})
        event = SourceObservation(work_id="work", source=source, cursor="2:400", provider_metadata_json=changed.model_dump_json())
        refresh_observation(database, event)
        database.rollback()
        assert database.execute("SELECT 1 FROM imap_seen WHERE uid_validity=2").fetchone() is None
        refresh_observation(database, event)
        database.commit()
        assert known_observation(database, source, changed) == 10
        saved = database.execute("SELECT labels_json FROM imap_seen WHERE uid_validity=2").fetchone()[0]
        assert ImapMessageMetadata.model_validate_json(saved).download == metadata.download
        assert totals(archive).archived == 10
        assert database.execute("SELECT parent FROM imap_mailboxes WHERE name='New/Label'").fetchone() == ("New",)


def test_reporter_updates_while_sync_is_running(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """File events publish committed totals before sync completes, without a periodic timer."""
    archive = tmp_path / "archive"
    create_empty_archive(archive)
    reported = Event()

    class ObservedReporter(SyncReporter):
        def display(self, status: str) -> None:
            super().display(status)
            if status == "running" and self.last_running is not None and self.last_running.archived == 10:
                reported.set()

    reporter = ObservedReporter(archive, False)
    reporter.start()
    try:
        with closing(sqlite3.connect(archive / "archive.sqlite3")) as database:
            initialize_state(database)
            database.execute("INSERT INTO imap_hourly(source_id,hour_utc,archived,duplicates) VALUES ('fixture','hour',10,2)")
            database.commit()
        assert reported.wait(2), "Archive commit did not wake the reporter"
        output = capsys.readouterr().err
        assert "running: newly archived=10 duplicate downloads=2" in output
    finally:
        reporter.finish("completed")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT delivery; native Windows console acceptance remains separate")
@pytest.mark.parametrize("scan", [False, True])
def test_cli_ctrl_c_checkpoints_and_next_invocation_resumes(tmp_path: Path, imap_server: DisposableImap, scan: bool) -> None:
    """Interrupt the real CLI process during downloads, then finish without duplicate publication."""
    if sys.platform == "win32":
        pytest.skip("POSIX SIGINT delivery; native Windows console acceptance remains separate")
    server = imap_server
    account = server.account
    assert account is not None
    archive = tmp_path / "archive"
    create_empty_archive(archive)
    config = load_archive_config(archive)
    config.imap_sources = [account]
    config.owner = OwnerRules(include=["demouser"])
    save_archive_config(archive, config)
    environment = os.environ.copy()
    environment["SSL_CERT_FILE"] = str(server.certificate)
    command = [sys.executable, "-c", CLI_DRIVER, "--archive", str(archive), "--workers", str(min(8, 2 * (os.cpu_count() or 1)))]
    if not scan:
        command.append("--no-scan")
    process: subprocess.Popen[bytes] | None = None
    try:
        with server.connect() as client:
            for mailbox in range(8):
                name = f"folder{mailbox}"
                assert client.create(name)[0] == "OK"
                for index in range(10):
                    raw = f"From: fixture@example.org\r\nSubject: {mailbox}-{index}\r\n\r\n".encode() + b"preserved\r\n" * 500
                    assert client.append(name, None, None, raw)[0] == "OK"
        server.pause_after_bodies(1)
        with (tmp_path / "interrupted.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=log, start_new_session=True)
            def released_first() -> bool:
                # A committed progress snapshot acknowledges catalog startup;
                # inspecting its SQLite files before that boundary can race setup.
                snapshots = read_ingest_history(archive).statuses
                if not snapshots or snapshots[0].processed_messages == 0:
                    return False
                with closing(sqlite3.connect(archive / "archive.sqlite3")) as database:
                    row = database.execute("SELECT sha256 FROM messages ORDER BY message_pk LIMIT 1").fetchone()
                return bool(row and not (archive / "objects" / row[0]).exists())
            released = wait_for_path_state([archive / "status", archive / "objects"],
                                           released_first, timeout=120)
            assert 0 < totals(archive).archived < 80, (tmp_path / "interrupted.log").read_text()
            assert released, "first committed message remained cached while download continued"
            assert server.fetch_paused.wait(10), "No real body request reached the server gate"
            os.killpg(process.pid, signal.SIGINT)
            wait_for_path_state([tmp_path / "interrupted.log"], lambda: "Interrupted; stopping downloads and saving progress…" in
                                (tmp_path / "interrupted.log").read_text(), timeout=10)
            server.release_bodies()
            assert process.wait(timeout=30) == 130, (tmp_path / "interrupted.log").read_text()
        interrupted_log = (tmp_path / "interrupted.log").read_text()
        assert "Traceback" not in interrupted_log and "Exception ignored" not in interrupted_log
        assert "SpawnProcess" not in interrupted_log
        assert totals(archive).errors == 0
        partial = totals(archive).archived
        assert 0 < partial < 80
        # Desktop recovery must describe the actual CLI interruption before a
        # user chooses to reconnect, including a readable nonsecret account.
        saved = unfinished_work(archive).imports
        assert len(saved) == 1 and saved[0].archived == partial
        assert saved[0].duplicates == 0 and not saved[0].interruption_estimated
        assert account.username in saved[0].sources[0] and account.server in saved[0].sources[0]
        resumed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, timeout=60)
        assert resumed.returncode == 0, resumed.stderr.decode()
        assert totals(archive).archived == 80
        assert totals(archive).duplicates == 0
        assert not unfinished_work(archive).source_roots
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()


def test_configuration_identity_changes_and_legacy_compatibility(tmp_path: Path) -> None:
    """Local-only settings stay version 2; changed endpoints cannot reuse remote cursors or credentials."""
    archive = tmp_path / "archive"
    create_empty_archive(archive)
    config = load_archive_config(archive)
    save_archive_config(archive, config)
    assert "imap_sources" not in (archive / "config.yaml").read_text()
    assert load_archive_config(archive).version == 2
    answers = iter(["example.org", "993", "implicit", "fixture@example.org"])
    account, password = prompt_account(prompt=lambda _text: next(answers), secret_prompt=lambda _text: "fixture")
    assert password == "fixture"
    config.imap_sources = [account]
    save_archive_config(archive, config)
    assert load_archive_config(archive).version == 3
    assert "fixture\n" not in (archive / "config.yaml").read_text()
    answers = iter(["other.example.org", "", "", ""])
    changed, _secret = prompt_account(account, prompt=lambda _text: next(answers), secret_prompt=lambda _text: "fixture")
    assert changed.id != account.id
    assert changed.credential_ref != account.credential_ref
    assert account.model_copy(update={"server": "other.example.org"}).credential_ref != account.credential_ref
    with pytest.raises(ValueError, match="unique"):
        config.imap_sources = [account, account]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY; Windows native password-prompt acceptance remains separate")
def test_cli_new_archive_prompts_save_secret_and_run(tmp_path: Path, imap_server: DisposableImap) -> None:
    """Drive real terminal prompts through --new, including hidden password entry and saved reuse."""
    if sys.platform == "win32":
        pytest.skip("POSIX PTY; Windows native password-prompt acceptance remains separate")
    import pty  # pylint: disable=import-outside-toplevel
    import select  # pylint: disable=import-outside-toplevel
    server = imap_server
    account = server.account
    assert account is not None
    archive = tmp_path / "new.mailarchive"
    environment = os.environ.copy()
    environment["SSL_CERT_FILE"] = str(server.certificate)
    master, slave = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    transcript = b""

    def answer(marker: bytes, value: bytes) -> None:
        nonlocal transcript
        deadline = time.monotonic() + 10
        while not transcript.endswith(marker) and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                transcript += os.read(master, 65536)
        assert transcript.endswith(marker), transcript.decode(errors="replace")
        os.write(master, value + b"\n")

    try:
        with server.connect() as client:
            assert client.append("INBOX", None, None, b"Subject: new archive\r\n\r\ncomplete\r\n")[0] == "OK"
        process = subprocess.Popen([sys.executable, "-c", CLI_DRIVER, "--new", str(archive), "--no-scan"],
            cwd=ROOT, env=environment, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        answer(b"IMAP server: ", b"localhost")
        answer(b"Port [993]: ", str(account.port).encode())
        answer(b"TLS mode (implicit/starttls) [implicit]: ", b"")
        answer(b"Username: ", b"demouser")
        answer(b"Password or app password (stored in the OS keyring): ", b"demopass")
        deadline = time.monotonic() + 20
        while process.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                transcript += os.read(master, 65536)
        assert process.wait(timeout=2) == 0, transcript.decode(errors="replace")
        assert b"demopass" not in transcript
        assert "demopass" not in (archive / "config.yaml").read_text()
        assert load_archive_config(archive).imap_sources[0].username == "demouser"
        assert totals(archive).archived == 1
        refused = subprocess.run([sys.executable, "-c", CLI_DRIVER, "--new", str(archive), "--no-scan"],
            cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        assert refused.returncode == 1
        assert b"archive already exists" in refused.stderr
        assert totals(archive).archived == 1
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize(("wire", "expected"), [
    (b"Subject: mixed\r\n\r\n\xff\x00line\rlone\nLF\r\nend", b"Subject: mixed\n\n\xff\x00line\rlone\nLF\nend"),
    (b"Subject: LF\n\nbody\rlone", b"Subject: LF\n\nbody\rlone"),
    (b"", b""),
])
def test_download_normalization_retains_lone_cr_and_wire_evidence(wire: bytes, expected: bytes) -> None:
    """Normalize before ingest hashing, without decoding, adding final LF, or stripping lone CR."""
    from mailarchiver.imap_source import normalize_download
    original = ImapMessageMetadata(uid_validity=1, uid=1)
    normalized, metadata = normalize_download(wire, original)
    assert normalized == expected
    assert original.download is None
    assert metadata.download == ImapDownload(source_sha256=hashlib.sha256(wire).hexdigest(),
        source_byte_length=len(wire), crlf_pairs=wire.count(b"\r\n"))
    assert normalize_download(normalized, original)[0] == normalized


def test_disposable_server_greeting_needs_no_reverse_dns(tmp_path: Path) -> None:
    """Offline fixture requirement: real TLS greeting succeeds with reverse DNS denied."""
    code = """
from pathlib import Path
import sys
from tests.imap_fixture import DisposableImap

def reject_reverse_dns(event, _arguments):
    if event == 'socket.gethostbyaddr':
        raise RuntimeError('reverse DNS is unavailable in the fixture')

sys.addaudithook(reject_reverse_dns)
server = DisposableImap(Path(sys.argv[1]), 'implicit')
server.start()
try:
    with server.connect() as client:
        assert b'Server ready localhost' in client.welcome
finally:
    server.close()
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT,
                            capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
