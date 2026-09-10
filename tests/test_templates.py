"""Tests for app.emailing.templates (placeholder substitution/validation)."""

from app.emailing.templates import (
    PERSON_PLACEHOLDERS,
    find_invalid_email_addresses,
    find_unknown_placeholders,
    person_placeholder_values,
    render_template,
    validate_person_placeholders,
)
from app.models.person import Person


def _person(
    first_name: str = "Anna",
    last_name: str = "Muster",
    salutation: str = "Frau",
    company: str = "",
    email: str = "anna@example.invalid",
) -> Person:
    """Build an unpersisted `Person` for use in tests."""
    return Person(
        id=1, salutation=salutation, company=company, first_name=first_name, last_name=last_name,
        contact_email=email, contact_phone="",
        billing_street="", billing_house_number="", billing_postal_code="",
        billing_city="", billing_country="CH",
        iban="", customer_number=123456, bkw_customer_number=None, paper_invoice=False,
        active=True, created_at="",
    )


def test_render_template_substitutes_known_placeholders():
    person = _person()
    rendered = render_template("Guten Tag {anrede} {nachname}", person_placeholder_values(person))
    assert rendered == "Guten Tag Frau Muster"


def test_render_template_leaves_unknown_placeholder_visible():
    rendered = render_template("Hallo {addresse}", {"vorname": "Anna"})
    assert rendered == "Hallo {addresse}"


def test_render_template_merges_extra_context_values():
    person = _person()
    values = {**person_placeholder_values(person), "leg": "LEG Ittigen"}
    rendered = render_template("{vorname} -- {leg}", values)
    assert rendered == "Anna -- LEG Ittigen"


def test_find_unknown_placeholders_detects_typo():
    unknown = find_unknown_placeholders("Hallo {addresse}, {vorname}", PERSON_PLACEHOLDERS)
    assert unknown == {"addresse"}


def test_find_unknown_placeholders_empty_when_all_known():
    unknown = find_unknown_placeholders("Hallo {vorname} {nachname}", PERSON_PLACEHOLDERS)
    assert unknown == set()


def test_validate_person_placeholders_flags_empty_used_field():
    with_salutation = _person(salutation="Frau")
    without_salutation = _person(salutation="")
    problems = validate_person_placeholders(
        "Guten Tag {anrede} {nachname}", [with_salutation, without_salutation]
    )
    assert [p.salutation for p, _ in problems] == [""]
    assert problems[0][1] == ["anrede"]


def test_validate_person_placeholders_ignores_unused_placeholder():
    """A person missing `company` is not flagged if the template never uses `{company}`."""
    person = _person(company="")
    problems = validate_person_placeholders("Guten Tag {vorname}", [person])
    assert problems == []


def test_validate_person_placeholders_empty_when_template_has_no_placeholders():
    problems = validate_person_placeholders("Kein Platzhalter hier.", [_person(salutation="")])
    assert problems == []


def test_find_invalid_email_addresses_flags_garbage():
    valid = _person(email="anna@example.invalid")
    no_at = _person(email="anna-example.invalid")
    no_dot = _person(email="anna@invalid")
    empty = _person(email="")
    invalid = find_invalid_email_addresses([valid, no_at, no_dot, empty])
    assert invalid == [no_at, no_dot, empty]


def test_find_invalid_email_addresses_empty_when_all_valid():
    assert find_invalid_email_addresses([_person(email="anna@example.invalid")]) == []
