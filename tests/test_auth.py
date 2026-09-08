"""Requirements: remote authorization is provider-aware, bounded, and credential-safe."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from mailarchiver.auth import (
    GMAIL_READONLY_SCOPE,
    AuthorizerError,
    ClientSecrets,
    DnsEvidence,
    MailProvider,
    MailboxAddress,
    build_parser,
    classify_provider,
    console_steps,
    detect_provider,
    existing_client_secrets,
    gcloud_plan,
    install_client_secrets,
    unavailable_provider_message,
)


def client_document(project_id: str = "mailarchiver-personal-1234abcd") -> str:
    return json.dumps(
        {
            "installed": {
                "client_id": "client.apps.googleusercontent.com",
                "project_id": project_id,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": "public-desktop-value",
                "redirect_uris": ["http://localhost"],
            }
        }
    )


@pytest.mark.parametrize(
    ("value", "normalized"),
    [
        ("simsong@gmail.com", "simsong@gmail.com"),
        (" Simsong@BasisTech.COM ", "Simsong@basistech.com"),
    ],
)
def test_mailbox_address_is_normalized(value: str, normalized: str) -> None:
    assert MailboxAddress.parse(value).address == normalized


@pytest.mark.parametrize("value", ["gmail.com", "a@@gmail.com", "a@localhost", "a@bad_domain.test"])
def test_invalid_mailbox_address_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        MailboxAddress.parse(value)


def test_google_workspace_mx_is_gmail() -> None:
    detection = classify_provider(
        DnsEvidence(domain="basistech.com", mx_hosts=("smtp.google.com",))
    )

    assert detection.provider is MailProvider.GMAIL
    assert detection.evidence == ("MX smtp.google.com",)


def test_outlook_autodiscover_identifies_m365_behind_mail_gateway() -> None:
    detection = classify_provider(
        DnsEvidence(
            domain="fas.harvard.edu",
            mx_hosts=("mx0a-00171101.pphosted.com",),
            autodiscover_targets=("autodiscover.outlook.com",),
        )
    )

    assert detection.provider is MailProvider.M365
    assert detection.evidence == ("Autodiscover autodiscover.outlook.com",)
    assert unavailable_provider_message(detection.provider) == "Microsoft Office not yet implemented."


def test_gateway_alone_does_not_guess_provider() -> None:
    detection = classify_provider(
        DnsEvidence(domain="example.edu", mx_hosts=("example.mx.proofpoint.com",))
    )

    assert detection.provider is MailProvider.UNKNOWN


def test_gmail_override_does_not_require_dns() -> None:
    detection = detect_provider(MailboxAddress.parse("person@example.test"), force_gmail=True)

    assert detection.provider is MailProvider.GMAIL
    assert detection.evidence == ("--gmail override",)


def test_requested_cli_forms_have_one_account_positional() -> None:
    parser = build_parser()

    automatic = parser.parse_args(["simsong@basistech.com"])
    overridden = parser.parse_args(["--gmail", "simsong@basistech.com"])

    assert automatic.account == "simsong@basistech.com"
    assert automatic.gmail is False
    assert overridden.account == "simsong@basistech.com"
    assert overridden.gmail is True


def test_release_client_is_shared_without_per_user_registration(tmp_path: Path) -> None:
    shared = tmp_path / "installed" / "gmail_client.json"
    shared.parent.mkdir()
    shared.write_text(client_document(), encoding="utf-8")

    selected = existing_client_secrets(
        MailboxAddress.parse("new.user@gmail.com"),
        config_root=tmp_path / "user-config",
        distributed_path=shared,
    )

    assert selected == shared


def test_missing_release_client_does_not_start_registration(tmp_path: Path) -> None:
    selected = existing_client_secrets(
        MailboxAddress.parse("new.user@gmail.com"),
        config_root=tmp_path / "user-config",
        distributed_path=tmp_path / "missing.json",
    )

    assert selected is None


def test_gcloud_plan_is_project_scoped_and_least_privilege() -> None:
    account = MailboxAddress.parse("simsong@gmail.com")
    plan = gcloud_plan(account, "mailarchiver-personal-1234abcd")

    assert plan.login == (
        "auth",
        "login",
        "simsong@gmail.com",
        "--brief",
        "--no-activate",
    )
    assert plan.create_project[:3] == (
        "projects",
        "create",
        "mailarchiver-personal-1234abcd",
    )
    assert plan.enable_gmail == (
        "services",
        "enable",
        "gmail.googleapis.com",
        "--project",
        "mailarchiver-personal-1234abcd",
        "--account",
        "simsong@gmail.com",
    )
    assert "config" not in plan.model_dump_json()
    assert plan.create_project[-2:] == ("--account", "simsong@gmail.com")


def test_console_plan_requests_only_gmail_readonly_and_desktop_client() -> None:
    steps = console_steps(
        MailboxAddress.parse("simsong@gmail.com"), "mailarchiver-personal-1234abcd"
    )

    assert all("project=mailarchiver-personal-1234abcd" in step.url for step in steps)
    assert "your own address" in steps[0].instruction
    assert "seven days" in steps[1].instruction
    assert GMAIL_READONLY_SCOPE in steps[2].instruction
    assert "Desktop app" in steps[3].instruction


def test_client_download_is_validated_and_installed_privately(tmp_path: Path) -> None:
    source = tmp_path / "client_secret.json"
    source.write_text(client_document(), encoding="utf-8")
    account = MailboxAddress.parse("simsong@gmail.com")

    destination = install_client_secrets(
        source,
        account,
        project_id="mailarchiver-personal-1234abcd",
        config_root=tmp_path / "config",
    )

    parsed = ClientSecrets.model_validate_json(destination.read_bytes())
    assert parsed.installed.project_id == "mailarchiver-personal-1234abcd"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700


def test_wrong_project_download_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "client_secret.json"
    source.write_text(client_document("other-project"), encoding="utf-8")

    with pytest.raises(AuthorizerError, match="download belongs to project other-project"):
        install_client_secrets(
            source,
            MailboxAddress.parse("simsong@gmail.com"),
            project_id="mailarchiver-personal-1234abcd",
            config_root=tmp_path / "config",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("client_id", "client.evil.example", "expected a Google OAuth client ID"),
        ("auth_uri", "https://accounts.google.evil.example/oauth", "authorization endpoint"),
        ("token_uri", "https://oauth2.googleapis.evil.example/token", "token endpoint"),
        ("redirect_uris", ["https://evil.example/callback"], "loopback redirect"),
    ],
)
def test_google_client_download_rejects_lookalike_endpoints(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    document = json.loads(client_document())
    document["installed"][field] = value
    source = tmp_path / "client_secret.json"
    source.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(AuthorizerError, match=message):
        install_client_secrets(
            source,
            MailboxAddress.parse("simsong@gmail.com"),
            config_root=tmp_path / "config",
        )


@pytest.mark.parametrize("address", ["person@gmail.com", "person@outlook.com"])
def test_known_domains_do_not_resolve_dns(address: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: decisive domain evidence must not depend on network availability."""
    from mailarchiver import auth

    def unavailable(_domain: str) -> DnsEvidence:
        raise AssertionError("DNS must not be consulted")

    monkeypatch.setattr(auth, "resolve_dns_evidence", unavailable)
    assert detect_provider(MailboxAddress.parse(address)).provider is not MailProvider.UNKNOWN


@pytest.mark.parametrize("negative", [True, False])
def test_dns_negative_answers_are_distinct_from_outages(
    negative: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement: resolver outages cannot be reported as absent provider records."""
    from mailarchiver import auth

    def resolve(*_args: object, **_kwargs: object) -> None:
        if negative:
            raise auth.dns.resolver.NoAnswer()
        raise auth.dns.exception.Timeout()

    monkeypatch.setattr(auth.dns.resolver.Resolver, "resolve", resolve)
    if negative:
        assert auth.resolve_dns_evidence("example.test").mx_hosts == ()
    else:
        with pytest.raises(AuthorizerError, match="DNS MX lookup failed"):
            auth.resolve_dns_evidence("example.test")


def test_client_install_uses_one_validated_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: a changed download cannot replace the validated client bytes."""
    source = tmp_path / "download.json"
    original = client_document().encode()
    source.write_bytes(original)
    read_bytes = Path.read_bytes

    def read_and_change(path: Path) -> bytes:
        payload = read_bytes(path)
        if path == source:
            path.write_bytes(b"unvalidated replacement")
        return payload

    monkeypatch.setattr(Path, "read_bytes", read_and_change)
    destination = install_client_secrets(
        source, MailboxAddress.parse("person@gmail.com"), config_root=tmp_path / "config"
    )
    assert read_bytes(destination) == original


@pytest.mark.parametrize("case", ["new", "mismatch", "refresh", "transport", "store-failure"])
def test_authorization_verifies_identity_before_persisting(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement: consent/refresh/profile/keyring ordering never stores a wrong identity.

    Substitutes are confined to external OAuth and OS keyring boundaries so no
    real account, browser consent, network, or user credential store is touched.
    """
    from datetime import datetime, timedelta, timezone
    from mailarchiver import auth

    account = MailboxAddress.parse("person@gmail.com")
    events: list[str] = []
    stored: list[str] = []

    class TestCredentials(auth.Credentials):
        def refresh(self, request: object) -> None:
            events.append("refresh")
            if case == "transport":
                raise auth.TransportError("offline")
            self.token = "refreshed-test-token"
            self.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    credentials = TestCredentials(
        token="test-token", refresh_token="test-refresh", token_uri=auth.GOOGLE_TOKEN_URI,
        client_id="test.apps.googleusercontent.com", client_secret="public",
        scopes=[GMAIL_READONLY_SCOPE],
    )
    credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        hours=-1 if case in {"refresh", "transport"} else 1
    )

    class Flow:
        def run_local_server(self, **_kwargs: object) -> auth.Credentials:
            events.append("consent")
            return credentials

    def profile(_credentials: auth.Credentials) -> auth.GmailProfile:
        events.append("profile")
        return auth.GmailProfile(
            emailAddress="wrong@gmail.com" if case == "mismatch" else account.address,
            messagesTotal=1, threadsTotal=1, historyId="1",
        )

    def store(_service: str, _username: str, value: str) -> None:
        events.append("store")
        if case == "store-failure":
            raise auth.KeyringError("locked")
        stored.append(value)

    monkeypatch.setattr(auth, "_load_credentials", lambda _account: credentials if case in {"refresh", "transport"} else None)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_secrets_file", lambda *_a, **_k: Flow())
    monkeypatch.setattr(auth, "_profile", profile)
    monkeypatch.setattr(auth.keyring, "set_password", store)
    if case in {"mismatch", "transport", "store-failure"}:
        with pytest.raises(AuthorizerError):
            auth.authorize_gmail(account, tmp_path / "client.json")
        assert stored == []
        if case == "transport":
            assert events == ["refresh"]
        elif case == "mismatch":
            assert events == ["consent", "profile"]
    else:
        assert auth.authorize_gmail(account, tmp_path / "client.json").email_address == account.address
        assert len(stored) == 1
        assert events == ["refresh" if case == "refresh" else "consent", "profile", "store"]
