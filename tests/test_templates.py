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
    vorname: str = "Anna",
    nachname: str = "Muster",
    anrede: str = "Frau",
    firma: str = "",
    email: str = "anna@example.invalid",
) -> Person:
    """Build an unpersisted `Person` for use in tests."""
    return Person(
        id=1, anrede=anrede, firma=firma, vorname=vorname, nachname=nachname,
        kontakt_email=email, kontakt_telefon="",
        rechnungsadresse_strasse="", rechnungsadresse_hausnummer="", rechnungsadresse_plz="",
        rechnungsadresse_ort="", rechnungsadresse_land="CH",
        iban="", kundennummer=123456, bkw_kundennummer=None, papierrechnung=False,
        aktiv=True, created_at="",
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
    with_anrede = _person(anrede="Frau")
    without_anrede = _person(anrede="")
    problems = validate_person_placeholders(
        "Guten Tag {anrede} {nachname}", [with_anrede, without_anrede]
    )
    assert [p.anrede for p, _ in problems] == [""]
    assert problems[0][1] == ["anrede"]


def test_validate_person_placeholders_ignores_unused_placeholder():
    """A person missing `firma` is not flagged if the template never uses `{firma}`."""
    person = _person(firma="")
    problems = validate_person_placeholders("Guten Tag {vorname}", [person])
    assert problems == []


def test_validate_person_placeholders_empty_when_template_has_no_placeholders():
    problems = validate_person_placeholders("Kein Platzhalter hier.", [_person(anrede="")])
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
