"""Placeholder substitution for email texts (`{vorname}`, `{anrede}`, ...)."""

import re
import sqlite3
from typing import Callable, Optional, Sequence

from app.domain.salutation import letter_salutation
from app.domain.substation_area_lookup import bkw_designations_for_person
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

#: Placeholder name -> value extractor needing the database, because the value
#: is not on `Person`. Kept apart from `PERSON_PLACEHOLDERS` for that reason
#: only: for whoever writes a text these are the same thing, so
#: `placeholder_values` hands out both and `ALL_PLACEHOLDERS` is the set a text
#: is validated against.
CONTEXT_PLACEHOLDERS: dict[str, Callable[[sqlite3.Connection, Person], str]] = {
    "trafokreis": lambda c, p: bkw_designations_for_person(c, p.id),
}

#: Every placeholder available in every email text, in display order.
ALL_PLACEHOLDERS = (*PERSON_PLACEHOLDERS, *CONTEXT_PLACEHOLDERS)

#: The invented Person every placeholder example is resolved against -- a
#: couple at a company, so that no placeholder comes out empty. Deliberately
#: fed through the real extractors: an example cannot then drift from what the
#: placeholder actually produces.
EXAMPLE_PERSON = Person(
    id=None,
    salutation="Herr",
    company="Muster AG",
    first_name="Hans",
    last_name="Muster",
    contact_email="hans.muster@example.invalid",
    contact_phone="031 000 00 00",
    billing_street="Musterweg",
    billing_house_number="4",
    billing_postal_code="3000",
    billing_city="Bern",
    billing_country="CH",
    iban="CH00 0000 0000 0000 0000 0",
    customer_number=123456,
    bkw_customer_number=None,
    paper_invoice=False,
    active=True,
    created_at="",
    second_salutation="Frau",
    second_first_name="Anna",
    second_last_name="Muster",
    second_contact_email="anna.muster@example.invalid",
)

#: Example values for every placeholder that is not derived from `Person` --
#: the database ones and the context ones each occasion adds (invoice, dunning
#: notice). A name missing here is shown with an empty example rather than a
#: made-up one.
EXAMPLE_VALUES: dict[str, str] = {
    "trafokreis": "TRA9365",
    "leg": "LEG Musterquartier",
    "quartal": "3",
    "jahr": "2026",
    "betrag": "142.65",
    "neue_frist": "31.12.2026",
}


def placeholder_examples(
    names: Sequence[str], values: Optional[dict[str, str]] = None
) -> list[tuple[str, str]]:
    """Resolve each named placeholder, for `EXAMPLE_PERSON` or for a real one.

    `values` are a real person's (`placeholder_values`), so a value that is
    empty for them stays empty -- which is the question being asked of the
    list. `EXAMPLE_VALUES` fills in only what no person can answer: an amount,
    a quarter or a deadline exists at the moment of the send, not before.
    """
    resolved = person_placeholder_values(EXAMPLE_PERSON) if values is None else values
    return [(name, resolved.get(name, EXAMPLE_VALUES.get(name, ""))) for name in names]


_PLACEHOLDER_PATTERN = re.compile(r"\{(\w+)\}")


class _SafeDict(dict):
    """A dict that leaves an unmatched `{key}` in the template untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def person_placeholder_values(person: Person) -> dict[str, str]:
    """Build the placeholder values available for one person."""
    return {name: extractor(person) for name, extractor in PERSON_PLACEHOLDERS.items()}


def placeholder_values(connection: sqlite3.Connection, person: Person) -> dict[str, str]:
    """Build every placeholder value for one person, database lookups included."""
    return {
        **person_placeholder_values(person),
        **{name: extractor(connection, person) for name, extractor in CONTEXT_PLACEHOLDERS.items()},
    }


def render_template(template: str, values: dict) -> str:
    """Substitute known `{placeholder}`s in a template with their values."""
    return template.format_map(_SafeDict(values))


#: Classic plain-text signature delimiter (RFC 3676) -- some mail clients
#: recognize "-- " on its own line and render or strip a trailing signature
#: specially (dimmed, or omitted from a reply quote).
SIGNATURE_DELIMITER = "\n\n-- \n"


def compose_with_signature(body: str, signature_content: str) -> str:
    """Append a signature to a message body, if one was chosen.

    Shared by the Rundmail and by a Textbaustein, so the same signature comes
    out looking the same either way -- it used to live in
    `app.gui.pages.email_dispatch`, where only that one page could reach it.
    """
    if not signature_content.strip():
        return body
    return f"{body}{SIGNATURE_DELIMITER}{signature_content}"


def find_unknown_placeholders(template: str, known_keys) -> set[str]:
    """Find `{placeholder}`s in a template that aren't in `known_keys`."""
    known = set(known_keys)
    return {name for name in _PLACEHOLDER_PATTERN.findall(template) if name not in known}


def validate_person_placeholders(
    template: str,
    recipients: list[Person],
    connection: Optional[sqlite3.Connection] = None,
) -> list[tuple[Person, list[str]]]:
    """Find recipients for whom a placeholder actually used in the template would render empty."""
    extractors: dict[str, Callable[[Person], str]] = dict(PERSON_PLACEHOLDERS)
    if connection is not None:
        for name, context_extractor in CONTEXT_PLACEHOLDERS.items():
            extractors[name] = lambda person, e=context_extractor: e(connection, person)
    used = [name for name in _PLACEHOLDER_PATTERN.findall(template) if name in extractors]
    if not used:
        return []
    problems = []
    for person in recipients:
        empty = [name for name in used if not extractors[name](person).strip()]
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
