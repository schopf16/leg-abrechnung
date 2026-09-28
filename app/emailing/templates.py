"""Placeholder substitution for email texts (`{vorname}`, `{anrede}`, ...).

Shared by the broadcast/LEG email composer and the invoice email template
(see `app.gui.pages.email_dispatch` and `app.gui.pages.billing`) -- one
substitution engine, one validation pass, used from both places.

Deliberately simple `str`-based `{placeholder}` syntax (not a templating
library): the audience is Michael typing a message in a textarea, not a
developer, so the syntax needs to be self-explanatory from a one-line
hint in the UI.
"""

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


def validate_person_placeholders(template: str, recipients: list[Person]) -> list[tuple[Person, list[str]]]:
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


def _is_plausible_address(email: str) -> bool:
    """Whether an address is plausible enough to hand to Graph.

    Args:
        email: The address to check.

    Returns:
        `True` if it contains an "@" with something before it and a "."
        somewhere after it. A lightweight check only -- not full RFC 5322
        validation.
    """
    at_index = email.find("@")
    return at_index > 0 and "." in email[at_index + 1 :]


def find_invalid_email_addresses(recipients: list[Person]) -> list[Person]:
    """Find recipients with an email address that is not even plausibly valid.

    `Person`'s address fields have no format validation at the model level,
    so a garbage value would otherwise only surface as a Graph API failure
    at send time.

    **Every** address of a person is checked, not just the first: a couple
    holds two, and the message goes to both in one send (see
    `app.emailing.graph_client.send_email`), so one bad address among two
    would take the whole message down. Reporting it is the point -- a
    person with one good and one broken address must not look fine.

    Args:
        recipients: Persons to check.

    Returns:
        The persons with at least one implausible address, in `recipients`
        order, and also those with no address at all -- the question this
        answers is whether a message can go out to them, and for both
        answers it cannot.
    """
    return [
        person
        for person in recipients
        if not person.contact_emails
        or any(not _is_plausible_address(address) for address in person.contact_emails)
    ]
