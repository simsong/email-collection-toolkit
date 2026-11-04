# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: each AWS validation request launches one terminating, report-uploading worker."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import base64
from urllib.parse import parse_qs
from pathlib import Path

import pytest

LAUNCHER_PATH = Path(__file__).parents[1] / "validation" / "aws" / "launcher" / "app.py"
SPEC = importlib.util.spec_from_file_location("validation_launcher", LAUNCHER_PATH)
assert SPEC is not None and SPEC.loader is not None
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


def request() -> dict[str, object]:
    return {
        launcher.DATASET_ID: "sf-lovers",
        launcher.RUN_ID: "20260829T120000Z-a1b2c3d4",
        launcher.INSTANCE_TYPE: "m7i.large",
        launcher.VOLUME_SIZE_GIB: 50,
    }


def test_launcher_rejects_extra_fields_and_shell_metacharacters() -> None:
    extra = request() | {"surprise": True}
    with pytest.raises(ValueError, match="Extra inputs"):
        launcher.parse_request(extra)

    malicious = request() | {launcher.DATASET_ID: "enron; shutdown now"}
    with pytest.raises(ValueError, match="dataset_id"):
        launcher.parse_request(malicious)


def test_worker_script_runs_pipeline_uploads_evidence_and_always_shuts_down() -> None:
    parsed = launcher.parse_request(request())
    script = launcher.worker_script(
        parsed,
        "validation-bucket",
        "mailarchiver-validation",
        "https://github.com/simsong/email-collection-toolkit.git",
        "codex/validation-datasets",
    )

    assert script.startswith("#!/bin/bash\nset -Eeuo pipefail")
    assert 'make validation-run DATASET="${dataset}"' in script
    assert "mailbag.zip" in script
    assert "run-report.json" in script
    assert "status.json" in script
    assert "trap finish EXIT" in script
    assert "shutdown -h now" in script
    assert "sshd" not in script
    assert "clamav-daemon" not in script and "CLAMD" not in script
    assert "MAILARCHIVER_CLAMAV_DATABASE=/var/lib/clamav" in script
    syntax = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True, check=False)
    assert syntax.returncode == 0, syntax.stderr


def test_ec2_sdk_encodes_user_data_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """AWS: actual botocore request serialization must deliver an executable shell script."""
    import boto3
    client = boto3.client("ec2", region_name="us-east-1", aws_access_key_id="fixture", aws_secret_access_key="fixture")
    bodies: list[str] = []

    class WireCaptured(Exception):
        pass

    def capture(request, **_unused):
        bodies.append(request.body.decode() if isinstance(request.body, bytes) else request.body)
        raise WireCaptured

    client.meta.events.register("before-send.ec2.RunInstances", capture)
    # Intercept only network delivery; boto3 validation, handlers and encoding are real.
    monkeypatch.setattr(boto3, "client", lambda _service: client)
    values = {"AMI_ID": "ami-12345678", "OUTPUT_BUCKET": "validation-bucket", "OUTPUT_PREFIX": "validation",
        "REPOSITORY_URL": "https://github.com/simsong/email-collection-toolkit.git", "REPOSITORY_REF": "fix-docs",
        "INSTANCE_PROFILE_ARN": "arn:aws:iam::123456789012:instance-profile/fixture", "SECURITY_GROUP_ID": "sg-12345678",
        "SUBNET_ID": "subnet-12345678"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    try:
        with pytest.raises(WireCaptured):
            launcher.lambda_handler(request(), None)
    finally:
        client.close()
    assert len(bodies) == 1
    encoded, = parse_qs(bodies[0])["UserData"]
    expected = launcher.worker_script(launcher.parse_request(request()), values["OUTPUT_BUCKET"], values["OUTPUT_PREFIX"],
        values["REPOSITORY_URL"], values["REPOSITORY_REF"])
    assert base64.b64decode(encoded).decode() == expected


def test_sam_control_plane_uses_real_terminating_ec2_and_external_bucket() -> None:
    """Requirement: SAM launches EC2 workers but does not own the result bucket."""
    template = (LAUNCHER_PATH.parents[2] / "template.yaml").read_text(encoding="utf-8")
    source = LAUNCHER_PATH.read_text(encoding="utf-8")

    assert "AWS::EC2::SecurityGroup" in template
    assert "AWS::IAM::InstanceProfile" in template
    assert "AWS::S3::Bucket" not in template
    assert 'InstanceInitiatedShutdownBehavior="terminate"' in source
    assert '"HttpTokens": "required"' in source
    assert '"Encrypted": True' in source
    assert '"DeleteOnTermination": True' in source
