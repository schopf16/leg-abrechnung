"""Tests for the one salutation both the invoice PDF and the emails use.

The reason this function exists is that every earlier mechanism produced a
wrong greeting: the PDF printed a fixed "Sehr geehrte Kundin, sehr geehrter
Kunde" naming nobody, and the email templates let the administrator assemble
"Sehr geehrte {anrede} {nachname}", which reads "Sehr geehrte Herr Muster"
for every man. So the cases below are not decoration -- each one is a way of
addressing somebody that has to come out right.
"""

import pytest

from app.domain.salutation import letter_salutation
from app.models.person import Person


def _person(**overrides) -> Person:
    """Build a Person with only the fields a salutation depends on.

    Args:
        **overrides: Fields to set on the person.

    Returns:
        An unpersisted `Person`.
    """
    fields = dict(
        id=1,
        salutation="",
        company="",
        first_name="",
        last_name="",
        contact_email="",
        contact_phone="",
        billing_street="",
        billing_house_number="",
        billing_postal_code="",
        billing_city="",
        billing_country="CH",
        iban="",
        customer_number=123456,
        bkw_customer_number=None,
        paper_invoice=False,
        active=True,
        created_at="",
    )
    fields.update(overrides)
    return Person(**fields)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        (
            {"salutation": "Frau", "first_name": "Anna", "last_name": "Muster"},
            "Guten Tag Frau Muster",
        ),
        (
            {"salutation": "Herr", "first_name": "Beat", "last_name": "Beispiel"},
            "Guten Tag Herr Beispiel",
        ),
        (
            {
                "salutation": "Frau",
                "first_name": "Anna",
                "last_name": "Muster",
                "second_salutation": "Herr",
                "second_first_name": "Beat",
                "second_last_name": "Beispiel",
            },
            "Guten Tag Frau Muster, guten Tag Herr Beispiel",
        ),
        (
            {
                "salutation": "Frau",
                "first_name": "Anna",
                "last_name": "Muster",
                "second_salutation": "Frau",
                "second_first_name": "Eva",
                "second_last_name": "Beispiel",
            },
            "Guten Tag Frau Muster, guten Tag Frau Beispiel",
        ),
        ({"first_name": "Anna", "last_name": "Muster"}, "Guten Tag Anna Muster"),
        ({"salutation": "Familie", "last_name": "Muster"}, "Guten Tag Familie Muster"),
        ({"company": "Hauswartung AG"}, "Guten Tag"),
        (
            {"company": "Hauswartung AG", "salutation": "Herr", "first_name": "Urs", "last_name": "Kohler"},
            "Guten Tag Herr Kohler",
        ),
    ],
)
def test_the_salutation_for_each_constellation(overrides, expected):
    """Every shape of Person this app can hold gets a correct greeting."""
    assert letter_salutation(_person(**overrides)) == expected


def test_a_second_person_without_a_salutation_is_greeted_by_name():
    """A missing salutation is a valid state, not a defect.

    Somebody who does not want to be sorted into Herr or Frau, or whose
    salutation was simply never recorded, still has to be addressable --
    which is the whole reason the greeting carries no inflected adjective.
    """
    person = _person(
        salutation="Frau",
        first_name="Anna",
        last_name="Muster",
        second_first_name="Kim",
        second_last_name="Beispiel",
    )
    assert letter_salutation(person) == "Guten Tag Frau Muster, guten Tag Kim Beispiel"


def test_the_greeting_never_carries_an_inflected_adjective():
    """The property that makes one rule work for every constellation.

    "Sehr geehrte/geehrter/geehrtes" has to agree with gender; "Guten Tag"
    does not. If somebody ever reintroduces the inflected form, this fails
    before a single letter goes out with the wrong ending.
    """
    for overrides in (
        {"salutation": "Herr", "last_name": "Muster"},
        {"salutation": "Frau", "last_name": "Muster"},
        {"salutation": "Familie", "last_name": "Muster"},
        {"company": "Hauswartung AG"},
    ):
        greeting = letter_salutation(_person(**overrides))
        assert "geehrt" not in greeting.lower(), greeting


def test_a_person_with_no_name_at_all_still_gets_a_salutation():
    """A letter without a greeting line is not an option."""
    assert letter_salutation(_person()) == "Guten Tag"


def test_the_second_greeting_keeps_its_capital_noun():
    """ "Tag" is a noun and stays capitalised mid-sentence.

    Regression test: lower-casing the whole greeting produced "guten tag".
    """
    person = _person(
        salutation="Frau",
        last_name="Muster",
        second_salutation="Herr",
        second_last_name="Beispiel",
    )
    assert "guten Tag Herr Beispiel" in letter_salutation(person)
    assert "guten tag" not in letter_salutation(person)
