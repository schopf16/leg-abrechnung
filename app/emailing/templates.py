"""Placeholder substitution for email texts (`{vorname}`, `{anrede}`, ...)."""

import re
from typing import Callable

from app.domain.salutation import letter_salutation
from app.models.person import Person

#: Placeholder name -> value extractor, available in every email text.
#:
#: `{briefanrede}` is the one to greet people with: it produces a complete,
#: correctly formed salutation for one person or for a couple (see
#: `app.domain.salutation`). The older parts -- `{anrede}`, `{vorname}`,
#: `{nachname}` -- remain because they are in the administrator's stored
#: templates, and they refer to the **first** named person. Note that
#: building a greeting out of them cannot be made correct: "Sehr geehrte
#: {anrede} {nachname}" reads "Sehr geehrte Herr Muster" for every man, and
#: for a couple there is no single inflection that works at all.
PERSON_PLACEHOLDERS: dict[str, Callable[[Person], str]] = {
    "briefanrede": letter_salutation,
    "anrede": lambda p: p.salutation,
    "vorname": lambda p: p.first_name,
    "nachname": lambda p: p.last_name,
    "anrede2": lambda p: p.second_salutation,
    "vorname2": lambda p: p.second_first_name,
    "nachname2": lambda p: p.second_last_name,
    "name": lambda p: p.display_name,
    "firma": lambda p: p.company,
    "kundennummer": lambda p: p.formatted_customer_number,
    "email": lambda p: ", ".join(p.contact_emails),
}

_PLACEHOLDER_PATTERN = re.compile(r"\{(\w+)\}")


class _SafeDict(dict):
    """A dict that leaves an unmatched `{key}` in the template untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def person_placeholder_values(person: Person) -> dict[str, str]:
    """Build the placeholder values available for one person."""
    return {name: extractor(person) for name, extractor in PERSON_PLACEHOLDERS.items()}


def render_template(template: str, values: dict) -> str:
    """Substitute known `{placeholder}`s in a template with their values."""
    return template.format_map(_SafeDict(values))


def find_unknown_placeholders(template: str, known_keys) -> set[str]:
    """Find `{placeholder}`s in a template that aren't in `known_keys`."""
    known = set(known_keys)
    return {name for name in _PLACEHOLDER_PATTERN.findall(template) if name not in known}


def validate_person_placeholders(template: str, recipients: list[Person]) -> list[tuple[Person, list[str]]]:
    """Find recipients for whom a placeholder actually used in the template would render empty."""
    used = [name for name in _PLACEHOLDER_PATTERN.findall(template) if name in PERSON_PLACEHOLDERS]
    if not used:
        return []
    problems = []
    for person in recipients:
        empty = [name for name in used if not PERSON_PLACEHOLDERS[name](person).strip()]
        if empty:
            problems.append((person, empty))
    return problems


def _is_plausible_address(email: str) -> bool:
    """Whether an address is plausible enough to hand to Graph."""
    at_index = email.find("@")
    return at_index > 0 and "." in email[at_index + 1 :]


def find_invalid_email_addresses(recipients: list[Person]) -> list[Person]:
    """Find recipients with an email address that is not even plausibly valid."""
    return [
        person
        for person in recipients
        if not person.contact_emails
        or any(not _is_plausible_address(address) for address in person.contact_emails)
    ]
