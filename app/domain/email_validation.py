"""Email address validation: only what can be stated without guessing.

Shaped after `app.domain.iban_validation`, but the problem is the opposite
one. An IBAN carries its own check digits, so a wrong one can be *proved*
wrong. An address cannot: whether a mailbox exists is knowable only by
sending to it, and this app must never send to a real address to find out.

So this reports the two things that are certainly wrong -- no `@` at all,
or nothing on one side of it -- and says nothing about anything else. No
pattern for the local part, no list of top-level domains, no "looks
suspicious". A false complaint about an address that works is worse than no
check: it trains the administrator to click past the warning, and then the
IBAN warning beside it gets clicked past too.

Why it exists at all: an invoice is announced by email, and
`app.emailing.send_service` reports one outcome per contract party. A
mistyped address fails at send time, long after the dialog was closed and
usually while a whole quarter is going out. The dialog is where the person
who knows the address is standing.

An empty value is valid. Most people in this database have no second email
address, and `Person.contact_emails` simply skips the empty ones.
"""

from typing import Optional


def validate_email(value: str) -> Optional[str]:
    """Report what is certainly wrong with an email address.

    Args:
        value: Raw user input. Empty or whitespace is accepted.

    Returns:
        A German message, or `None` when nothing can be said against it.
    """
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
