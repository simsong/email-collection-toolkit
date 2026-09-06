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
