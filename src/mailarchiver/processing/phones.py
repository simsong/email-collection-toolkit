# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Extract displayed phone candidates from the identity processor's bounded text.
# Retain the observed spelling and a digits-only comparison form with optional +.
# Do not infer a country, owner, dialability, extension, or verified personal number.
# Trim replies before signature selection and exclude date-shaped strings.
# The host associates these derived observations with the message sender.
# Original messages remain unchanged; picker rows deduplicate normalized numbers.
"""Bounded, country-neutral signature phone observations."""
from __future__ import annotations

import re

from .contracts import PhoneNumber

PHONE = re.compile(r"(?<![\w+])(?:\+?\d|\(\d)[\d ()./-]*\d(?!\w)")
DATE = re.compile(r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})")
REPLY = re.compile(r"^(?:>|on .{0,160}wrote:|from:\s|sent:\s|-+\s*(?:original|forwarded) message|begin forwarded message:)", re.IGNORECASE)


def _candidates(value: str) -> tuple[str, ...]:
    # Split a slash list only when every part is independently phone-shaped.
    # Otherwise retain area-code separators such as 030/1234567 intact.
    parts = tuple(part.strip() for part in value.split("/"))
    if len(parts) > 1 and all(PHONE.fullmatch(part) and
            7 <= sum(character.isdecimal() for character in part) <= 15 for part in parts):
        return parts
    return (value,)


def signature_phone_numbers(text: str, signature_lines: int | None = None) -> tuple[PhoneNumber, ...]:
    lines: list[str] = []
    for line in text.splitlines():
        if REPLY.match(line.strip()):
            break
        lines.append(line)
    signature = "\n".join(lines)
    if signature_lines is not None:
        signature = signature.rsplit("\n-- \n", 1)[-1] if "\n-- \n" in signature else "\n".join(lines[-signature_lines:])
    phones: dict[str, PhoneNumber] = {}
    for line in signature.splitlines():
        for match in PHONE.finditer(line):
            for candidate in _candidates(match.group()):
                display = candidate.strip(" .-/")
                digits = "".join(character for character in display if character.isdecimal())
                if not 7 <= len(digits) <= 15 or len(display) > 64 or DATE.fullmatch(display):
                    continue
                normalized = ("+" if display.startswith("+") else "") + digits
                phones.setdefault(normalized, PhoneNumber(normalized=normalized, display=display))
    return tuple(phones.values())
