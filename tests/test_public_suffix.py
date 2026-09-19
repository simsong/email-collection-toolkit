# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Identity evidence requires offline organization domains with PSL wildcard/exception rules."""
import pytest

from mailarchiver.public_suffix import registrable_domain


@pytest.mark.parametrize(("domain", "expected"), [
    ("mail.example.co.uk", "example.co.uk"),
    ("a.b.ck", "a.b.ck"),
    ("www.ck", "www.ck"),
    ("mail.www.ck", "www.ck"),
    ("mail.city.kawasaki.jp", "city.kawasaki.jp"),
    ("a.b.kawasaki.jp", "a.b.kawasaki.jp"),
    ("user.blogspot.com", "blogspot.com"),  # Match the previous ICANN-only policy.
    ("MAIL.EXAMPLE.COM.", "example.com"),
    ("mail.bücher.de", "xn--bcher-kva.de"),
    ("mail.example.invalid", "example.invalid"),
    ("localhost", "localhost"),
])
def test_organization_domain_rules(domain: str, expected: str) -> None:
    assert registrable_domain(domain) == expected
