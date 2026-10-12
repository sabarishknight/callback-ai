"""Phone-number helpers and the test-mode safety check."""

import re

from .config import Settings

# E.164 = "+" then country code then number, 8–15 digits total. e.g. +919876543210
E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(raw: str) -> str:
    """Clean up a phone number into E.164 format, or raise ValueError.

    "098765 43210" -> "+919876543210"   (10-digit Indian mobile, leading 0 dropped)
    "+91 98765-43210" -> "+919876543210"
    """
    number = re.sub(r"[\s\-().]", "", raw or "")
    if number.startswith("00"):
        number = "+" + number[2:]
    if not number.startswith("+"):
        number = number.lstrip("0")
        if len(number) == 10:
            number = "+91" + number  # assume India for bare 10-digit numbers
        else:
            number = "+" + number
    if not E164_PATTERN.match(number):
        raise ValueError(f"Not a valid phone number: {raw!r}")
    return number


def check_call_allowed(number: str, settings: Settings) -> tuple[bool, str]:
    """Return (allowed?, reason). In test mode only allow-listed numbers pass."""
    if not settings.test_mode:
        return True, "live mode"
    allowed = set()
    for item in settings.allowed_numbers:
        try:
            allowed.add(normalize_phone(item))
        except ValueError:
            print(f"Warning: ignoring invalid ALLOWED_NUMBERS entry {item!r}")
    if number in allowed:
        return True, "on the test allow-list"
    return False, "TEST_MODE is on and this number is not in ALLOWED_NUMBERS"


def mask_phone(number: str) -> str:
    """Hide the middle digits so logs and the dashboard don't expose numbers."""
    if len(number) <= 6:
        return number
    return number[:3] + "•" * (len(number) - 7) + number[-4:]
