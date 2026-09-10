"""Placeholder substitution for email texts (`{first_name}`, `{salutation}`, ...).

Shared by the broadcast/LEG email composer and the invoice email template
(see `app.gui.pages.email_versand` and `app.gui.pages.abrechnung`) -- one
substitution engine, one validation pass, used from both places.

Deliberately simple `str`-based `{placeholder}` syntax (not a templating
library): the audience is Michael typing a message in a textarea, not a
developer, so the syntax needs to be self-explanatory from a one-line
hint in the UI.
"""

import re
from typing import Callable

from app.models.person import Person

#: Placeholder name -> value extractor, available in every email text.
PERSON_PLACEHOLDERS: dict[str, Callable[[Person], str]] = {
    "anrede": lambda p: p.salutation,
    "vorname": lambda p: p.first_name,
    "nachname": lambda p: p.last_name,
    "firma": lambda p: p.company,
    "kundennummer": lambda p: p.formatted_customer_number,
    "email": lambda p: p.contact_email,
}

_PLACEHOLDER_PATTERN = re.compile(r"\{(\w+)\}")


class _SafeDict(dict):
    """A dict that leaves an unmatched `{key}` in the template untouched.

    Used so a typo'd placeholder (`{addresse}`) doesn't crash
    `str.format_map` -- it stays visibly wrong in the rendered text
    instead, which `find_unknown_placeholders` then turns into an
    explicit warning shown to the user.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def person_placeholder_values(person: Person) -> dict[str, str]:
    """Build the placeholder values available for one person.

    Args:
        person: Person to extract values from.

    Returns:
        `{placeholder_name: value}` for every key in `PERSON_PLACEHOLDERS`.
    """
    return {name: extractor(person) for name, extractor in PERSON_PLACEHOLDERS.items()}


def render_template(template: str, values: dict) -> str:
    """Substitute known `{placeholder}`s in a template with their values.

    Args:
        template: Raw text containing zero or more `{placeholder}`s.
        values: `{placeholder_name: value}` mapping (e.g. from
            `person_placeholder_values`, optionally merged with extra
            context values such as invoice amounts).

    Returns:
        The text with every known placeholder replaced. An unknown
        placeholder is left as literal `{text}` rather than raising --
        see `find_unknown_placeholders` for surfacing that as a warning.
    """
    return template.format_map(_SafeDict(values))


def find_unknown_placeholders(template: str, known_keys) -> set[str]:
    """Find `{placeholder}`s in a template that aren't in `known_keys`.

    Args:
        template: Raw text to scan.
        known_keys: Iterable of valid placeholder names for this context.

    Returns:
        The set of unrecognized placeholder names actually used (e.g.
        `{"addresse"}` for a typo'd `{addresse}`), empty if none.
    """
    known = set(known_keys)
    return {name for name in _PLACEHOLDER_PATTERN.findall(template) if name not in known}


def validate_person_placeholders(
    template: str, recipients: list[Person]
) -> list[tuple[Person, list[str]]]:
    """Find recipients for whom a placeholder actually used in the
    template would render empty.

    Args:
        template: Raw text to check (only placeholders it actually uses
            are checked -- an unused `{company}` never triggers a warning
            just because some recipient has no `company`).
        recipients: Persons the email would be sent to.

    Returns:
        `(person, [empty_placeholder_names])` for every recipient with at
        least one empty used placeholder, in `recipients` order. Empty if
        none.
    """
    used = [name for name in _PLACEHOLDER_PATTERN.findall(template) if name in PERSON_PLACEHOLDERS]
    if not used:
        return []
    problems = []
    for person in recipients:
        empty = [name for name in used if not PERSON_PLACEHOLDERS[name](person).strip()]
        if empty:
            problems.append((person, empty))
    return problems


def find_invalid_email_addresses(recipients: list[Person]) -> list[Person]:
    """Find recipients whose `contact_email` is not even plausibly valid.

    `Person.contact_email` has no format validation at the model level, so
    a garbage value would otherwise only surface as a Graph API failure
    at send time. This is a lightweight plausibility check only (contains
    "@", a "." somewhere after it) -- not full RFC 5322 validation.

    Args:
        recipients: Persons to check.

    Returns:
        The persons whose email address fails the plausibility check, in
        `recipients` order. Empty if none.
    """
    invalid = []
    for person in recipients:
        email = person.contact_email.strip()
        at_index = email.find("@")
        if at_index <= 0 or "." not in email[at_index + 1 :]:
            invalid.append(person)
    return invalid
