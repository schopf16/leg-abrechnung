"""Formatting for Swiss phone numbers used on Person records."""

import re


def format_swiss_phone(value: str) -> str:
    """Format a Swiss number as ``+41 31 123 45 67`` when its length is known.

    International numbers and incomplete or unusual entries are returned
    unchanged apart from trimming surrounding whitespace.
    """
    original = (value or "").strip()
    digits = re.sub(r"\D", "", original)
    if digits.startswith("0041"):
        digits = digits[4:]
    elif digits.startswith("41") and original.startswith("+"):
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 10:
        digits = digits[1:]
    else:
        return original
    if len(digits) == 10 and digits.startswith("0"):
        # "+41 (0)31 ..." -- the trunk zero written after the country code.
        digits = digits[1:]
    if len(digits) != 9:
        return original
    return f"+41 {digits[:2]} {digits[2:5]} {digits[5:7]} {digits[7:9]}"
