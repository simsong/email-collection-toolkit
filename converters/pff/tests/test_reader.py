# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""PST/OST requirements: class handling and normalized reconstructed fields."""
from datetime import datetime, timezone

import pytest
from pff_converter.reader import body_charset, is_mail_class, mbox_date, message_id_valid, normalized_subject


@pytest.mark.parametrize(("message_class", "expected"), [
    ("ipm.note.Custom", True), ("REPORT.IPM.Note.NDR", True), ("IPM.Schedule.Meeting.Request", False),
    ("IPM.Contact", False), ("IPM.Appointment.Custom", False), ("IPM.Microsoft.ScheduleData.FreeBusy", False),
])
def test_mapi_mail_class_scope(message_class: str, expected: bool) -> None:
    """Mail and recognized non-mail families are delimited, case-insensitive class prefixes."""
    assert is_mail_class(message_class) is expected


@pytest.mark.parametrize("message_class", ["IPM.Noteish", "IPM.Unknown", "IPM.Contacts"])
def test_unknown_mapi_classes_are_incomplete_not_silently_excluded(message_class: str) -> None:
    """An unrecognized class may carry mail and must never count as a successful non-mail exclusion."""
    with pytest.raises(ValueError, match="unsupported MAPI message class"):
        is_mail_class(message_class)


def test_reconstructed_identifiers_subjects_and_mbox_dates_follow_rust_rules() -> None:
    """PST primary headers use the same safe forms as the Rust reconstruction."""
    assert message_id_valid("<local@example.test>")
    assert not message_id_valid("local@example.test")
    assert not message_id_valid("<local @example.test>")
    assert normalized_subject("\x01\x04Re: =?UTF-8?B?Y2Fmw6k=?=") == "Re: café"
    assert normalized_subject("Subject with trailing space ") == "Subject with trailing space "
    assert mbox_date(datetime(2026, 9, 20, 15, 4, 5, tzinfo=timezone.utc)) == "Sun Sep 20 15:04:05 2026"
    assert body_charset(99999, b"\xff") == "windows-1252"
