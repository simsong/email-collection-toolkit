# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""PST/OST requirement: distinguish mail classes without silently dropping unknown items."""
import pytest
from pff_converter.reader import is_mail_class


@pytest.mark.parametrize(("message_class", "expected"), [
    ("ipm.note.Custom", True), ("REPORT.IPM.Note.NDR", True), ("IPM.Schedule.Meeting.Request", True),
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
