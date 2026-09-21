# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""PST/OST requirements: class handling and normalized reconstructed fields."""
import io
import time
from datetime import datetime, timezone

import pytest
from pff_converter.reader import (_MimeWriter, PffSettings, body_charset, header,
                                 is_mail_class, mbox_date, message_id_valid,
                                 normalized_subject, transport_fields, transport_header)


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


def test_mime_parts_and_copied_headers_are_not_rfc2047_reencoded() -> None:
    """PST reconstructed headers and attachments use the Rust serializer's readable forms."""
    writer = _MimeWriter(PffSettings(), time.monotonic() + 1)
    header(writer, "X-Test", "<identifier@example.test>")
    writer.part("image/png", io.BytesIO(b"png"), filename="image001.png",
                content_id="image001.png@example.test")
    assert writer.output.getvalue() == (
        b"X-Test: <identifier@example.test>\r\n"
        b"Content-Type: image/png\r\nContent-Transfer-Encoding: base64\r\n"
        b"Content-ID: <image001.png@example.test>\r\n"
        b"Content-Disposition: inline;\r\n filename*0*=UTF-8''%69%6D%61%67%65%30%30%31%2E%70%6E%67\r\n\r\n"
        b"cG5n\r\n\r\n"
    )
    assert transport_fields("X-Test: \r\n value\r\n") == [("X-Test", " \r\n value")]
    transport_header(writer, "X-Test", " \r\n value")
    assert writer.output.getvalue().endswith(b"X-Test: \r\n value\r\n")
