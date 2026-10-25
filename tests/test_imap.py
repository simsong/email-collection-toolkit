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
from datetime import UTC, datetime
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import sqlite3
import os
import signal
import subprocess
import sys
import time
from threading import Barrier, Event

import pytest

from mailarchiver.__main__ import IngestRequest
from mailarchiver.application import create_empty_archive
from mailarchiver.archive_config import load_archive_config, save_archive_config
from mailarchiver.imap_cli import SyncReporter, parser, synchronize
from mailarchiver.imap_credentials import save_password
from mailarchiver.imap_credentials import PASSWORD_KEY, password_for, _development_password
from keyring.errors import KeyringError
from mailarchiver.imap_models import ImapAccount, ImapDownload, ImapMailbox, ImapMessageMetadata
from mailarchiver.imap_cli import prompt_account
from mailarchiver.imap_protocol import ImapSession, body_literal, decode_mailbox, message_metadata, response_tokens, selected_mailboxes
from mailarchiver.imap_state import initialize_state, known_observation, record_download, refresh_observation, totals
from mailarchiver.gui_processing import resume_request, unfinished_work
from mailarchiver.gui_app import IngestWindowApi
from mailarchiver.writer_lock import WriterLease
from mailarchiver.gui_service import SearchPage, search_page
from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.imap_source import ImapSourcePlugin, imap_locator
from mailarchiver.standalone_verify import verify_archive
from tests.test_gui_processing import open_headless, wait_for_import
from mailarchiver.mbox import MboxLocation, read_verified_location
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.plugin_api import MailContainer, MailObject, SourceObservation, SourceReference, SourceSpec

from imap_fixture import DisposableImap, FixtureKeyring


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


@pytest.fixture(autouse=True)
def imap_server_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real loopback server's greeting independent of host reverse DNS."""
    monkeypatch.setenv("FQDN", "localhost")


@pytest.fixture
def imap_server(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[DisposableImap]:
    """Pytest owns the isolated TLS listener, including cleanup after setup/assertion failures."""
    tls = getattr(request, "param", "implicit")
    assert tls in {"implicit", "starttls"}
    server = DisposableImap(tmp_path, "implicit" if tls == "implicit" else "starttls")
    try:
        server.start()
        yield server
    finally:
        server.close()


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
    assert args.workers == 8
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
        release.wait(10)

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
        assert request.index_attachments and request.workers == 8
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
    args.workers = 3
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
    """Periodic reporting reads current committed totals instead of waiting for run completion."""
    archive = tmp_path / "archive"
    create_empty_archive(archive)
    reporter = SyncReporter(archive, False, interval=0.02)
    reporter.start()
    try:
        with closing(sqlite3.connect(archive / "archive.sqlite3")) as database:
            initialize_state(database)
            database.execute("INSERT INTO imap_hourly(source_id,hour_utc,archived,duplicates) VALUES ('fixture','hour',10,2)")
            database.commit()
        deadline = time.monotonic() + 2
        output = ""
        while "running: newly archived=10 duplicate downloads=2" not in output and time.monotonic() < deadline:
            time.sleep(0.01)
            output += capsys.readouterr().err
        assert "running: newly archived=10 duplicate downloads=2" in output
    finally:
        reporter.finish("completed")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT delivery; native Windows console acceptance remains separate")
@pytest.mark.parametrize("scan", [False, True])
def test_cli_ctrl_c_checkpoints_and_next_invocation_resumes(tmp_path: Path, imap_server: DisposableImap, scan: bool) -> None:
    """Interrupt the real CLI process during downloads, then finish without duplicate publication."""
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
    command = [sys.executable, "-c", CLI_DRIVER, "--archive", str(archive), "--workers", "8"]
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
        with (tmp_path / "interrupted.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=log, start_new_session=True)
            deadline = time.monotonic() + 120
            released = False
            while process.poll() is None and time.monotonic() < deadline:
                with closing(sqlite3.connect(archive / "archive.sqlite3")) as database:
                    row = database.execute("SELECT sha256 FROM messages ORDER BY message_pk LIMIT 1").fetchone()
                if row and not (archive / "objects" / row[0]).exists():
                    released = True
                    break
                time.sleep(0.01)
            assert 0 < totals(archive).archived < 80, (tmp_path / "interrupted.log").read_text()
            assert released, "first committed message remained cached while download continued"
            os.killpg(process.pid, signal.SIGINT)
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
