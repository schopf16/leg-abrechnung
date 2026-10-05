"""Email address validation: only what can be stated without guessing."""

from typing import Optional


def validate_email(value: str) -> Optional[str]:
    """Report what is certainly wrong with an email address."""
    address = (value or "").strip()
    if not address:
        return None

    if address.count("@") != 1:
        return "E-Mail-Adresse: genau ein „@“ erwartet."

    local, _, domain = address.partition("@")
    if not local or not domain:
        return "E-Mail-Adresse: vor und nach dem „@“ muss etwas stehen."
    if "." not in domain:
        return "E-Mail-Adresse: nach dem „@“ fehlt der Punkt (z. B. example.ch)."
    if any(character.isspace() for character in address):
        return "E-Mail-Adresse: enthält ein Leerzeichen."

    return None
