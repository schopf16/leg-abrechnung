"""Tests for the address hints: what is flagged, and what a click does.

The two halves worth testing are the ones a rendered page cannot show.

**The dismissal has to expire by itself.** "Nein" records the confirmed
*value*, not a flag and not a date, so the hint comes back the moment the
text changes. Had it been a date, dismissing a hint would keep silencing a
different wrong address forever, and a tick that no longer holds is worse
than no tick.

**A click has to fill, and no click has to leave the text alone.** That is
the promise the suggestion list makes, and it is exactly the kind of thing
`2a33e40` broke for nine days while every page still rendered: the handlers
are therefore driven directly rather than looked at.
"""

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain.address_check import (
    KIND_PERSON,
    KIND_SITE,
    address_signature,
    find_address_issues,
)
from app.domain.address_lookup import FIELD_LOCALITY, suggest_addresses
from app.gui.address_hints import apply_suggestion
from app.gui.address_input import SuggestionBox
from app.models import person as person_repo
from app.models import site as site_repo
from app.models.person import Person
from app.models.site import Site


def _site(street: str = "Erstweg", number: str = "4", locality: str = "Musterdorf") -> int:
    """Create a site through `connection_scope`.

    The dialogs and the hint card open their own connection, so the data has
    to live in the scratch database `conftest.py` points them at -- the `db`
    fixture is a separate in-memory one.

    Args:
        street: Street name.
        number: House number.
        locality: What goes in the Ort field.

    Returns:
        The new site's id.
    """
    with connection_scope() as connection:
        return site_repo.create(
            connection,
            Site(
                id=None,
                street=street,
                house_number=number,
                postal_code="3048",
                municipality=locality,
                address_detail="",
                substation_area_id=None,
                created_at="",
            ),
        )


def _person(street: str = "Erstweg", locality: str = "Musterdorf", country: str = "CH") -> int:
    """Create a person with a billing address.

    Args:
        street: Billing street.
        locality: Billing locality.
        country: Billing country code.

    Returns:
        The new person's id.
    """
    with connection_scope() as connection:
        return person_repo.create(
            connection,
            Person(
                id=None,
                salutation="Frau",
                company="",
                first_name="Anna",
                last_name="Muster",
                contact_email="anna@example.invalid",
                contact_phone="",
                billing_street=street,
                billing_house_number="4",
                billing_postal_code="3048",
                billing_city=locality,
                billing_country=country,
                iban="",
                customer_number=None,
                bkw_customer_number=None,
                paper_invoice=False,
                active=True,
                created_at="",
            ),
        )


def _issues():
    """Collect the current issues.

    Returns:
        The list of `AddressIssue`.
    """
    with connection_scope() as connection:
        return find_address_issues(connection)


# --- What gets flagged ----------------------------------------------------


def test_a_correct_address_is_not_flagged(address_register):
    """86 of 92 real sites are already right; silence is the normal case."""
    _site()

    assert _issues() == []


def test_a_sites_locality_is_flagged_with_the_postal_name(address_register):
    """The political municipality is correct and still not what is wanted."""
    _site(locality="Grossgemeinde")

    issues = _issues()

    assert [(i.kind, i.finding.field, i.finding.suggestion) for i in issues] == [
        (KIND_SITE, FIELD_LOCALITY, "Musterdorf")
    ]
    assert issues[0].question == "Meinten Sie: Musterdorf?"


def test_a_persons_billing_address_is_flagged_too(address_register):
    """Reversed from the first design on purpose.

    Leaving street and house number unchecked for persons would have spared
    the occasional PO box one click and left every ordinary typo in a
    billing address standing.
    """
    _person(street="Nirgendweg")

    assert [i.kind for i in _issues()] == [KIND_PERSON]


def test_a_foreign_billing_address_is_left_alone(address_register):
    """A Swiss register says nothing about an address abroad."""
    _person(street="Nirgendweg", country="DE")

    assert _issues() == []


def test_a_question_without_a_suggestion_states_the_fact(address_register):
    """A genuinely new building has no official spelling to offer."""
    _site(street="Nirgendweg")

    assert _issues()[0].question == "Nicht im amtlichen Verzeichnis."


def test_nothing_is_flagged_without_a_register():
    """No `address_register` fixture here: the guard in conftest applies."""
    _site(locality="Grossgemeinde")

    assert _issues() == []


# --- Nein, and what revives it --------------------------------------------


def test_a_dismissed_locality_stays_quiet(address_register):
    """One click has to settle it, or the list becomes noise."""
    site_id = _site(locality="Grossgemeinde")

    with connection_scope() as connection:
        site_repo.confirm_locality(connection, site_id, "Grossgemeinde")

    assert _issues() == []


def test_changing_the_text_brings_the_hint_back(address_register):
    """The reason the confirmed *value* is stored rather than a flag.

    A flag or a date would keep silencing a hint about a value that is no
    longer there.
    """
    site_id = _site(locality="Grossgemeinde")
    with connection_scope() as connection:
        site_repo.confirm_locality(connection, site_id, "Grossgemeinde")
    assert _issues() == []

    with connection_scope() as connection:
        site = site_repo.get(connection, site_id)
        site.municipality = "Falschdorf"
        site_repo.update(connection, site)

    assert [i.finding.value for i in _issues()] == ["Falschdorf"]


def test_dismissing_the_street_does_not_silence_the_locality(address_register):
    """Two independent findings, so two separate confirmations."""
    site_id = _site(street="Postfach", locality="Grossgemeinde")
    signature = address_signature("Postfach", "4", "3048")

    with connection_scope() as connection:
        site_repo.confirm_address(connection, site_id, signature)

    assert [i.finding.field for i in _issues()] == [FIELD_LOCALITY]


def test_a_dismissed_po_box_survives_an_unrelated_edit(address_register):
    """`update` must not touch the confirmation columns.

    Editing the Lage field would otherwise rebuild the record from a fresh
    dataclass and quietly clear the dismissal.
    """
    site_id = _site(street="Postfach")
    with connection_scope() as connection:
        site_repo.confirm_address(connection, site_id, address_signature("Postfach", "4", "3048"))

    with connection_scope() as connection:
        site = site_repo.get(connection, site_id)
        site.address_detail = "3. Obergeschoss"
        site_repo.update(connection, site)

    assert _issues() == []


def test_a_persons_dismissal_works_the_same_way(address_register):
    """Same mechanism, so there is nothing new to get wrong."""
    person_id = _person(street="Postfach")
    with connection_scope() as connection:
        person_repo.confirm_billing_address(connection, person_id, address_signature("Postfach", "4", "3048"))

    assert _issues() == []


# --- The click path on the suggestion list --------------------------------


def _fields(register):
    """Build four inputs wired to a `SuggestionBox`.

    Args:
        register: Path of the test register.

    Returns:
        `(box, street, house_number, postal_code, locality)`.
    """
    client = Client(ui.page("/probe-address-input")(lambda: None), request=None)
    with client:
        street = ui.input("Adresse")
        house_number = ui.input("Hausnummer")
        postal_code = ui.input("PLZ")
        locality = ui.input("Ort")
        box = SuggestionBox(street, postal_code, locality, house_number, path=register)
    return box, street, house_number, postal_code, locality


def test_clicking_a_suggestion_fills_every_field(address_register):
    """The promise the list makes."""
    box, street, house_number, postal_code, locality = _fields(address_register)
    street.value = "Erstweg 4"
    box.update()

    box.apply(box.suggestions[0])

    assert (street.value, house_number.value) == ("Erstweg", "4")
    assert (postal_code.value, locality.value) == ("3048", "Musterdorf")


def test_without_a_click_the_typed_text_survives(address_register):
    """The other half, and the reason this is an input and not a select.

    An address the register does not know has to remain typeable, so
    nothing may be written until a suggestion is actually chosen.
    """
    box, street, house_number, postal_code, locality = _fields(address_register)
    street.value = "Eigenerweg"
    house_number.value = "77"

    box.update()

    assert (street.value, house_number.value) == ("Eigenerweg", "77")
    assert (postal_code.value, locality.value) in ((None, None), ("", ""))


def test_a_locality_suggestion_leaves_the_street_alone(address_register):
    """Picking "3048 Musterdorf" must not wipe a street already typed."""
    box, street, house_number, postal_code, locality = _fields(address_register)
    street.value = "Eigenerweg"
    box.update_locality(postal_code)
    postal_code.value = "3048"
    box.update_locality(postal_code)

    box.apply(box.suggestions[0])

    assert street.value == "Eigenerweg"
    assert (postal_code.value, locality.value) == ("3048", "Musterdorf")


def test_the_number_field_narrows_the_suggestions(address_register):
    """Typing the number in its own field has to work like typing it inline."""
    box, street, house_number, _, _ = _fields(address_register)
    street.value = "Erstweg"
    house_number.value = "31"

    box.update()

    assert [s.house_number for s in box.suggestions] == ["31.1"]


def test_suggestions_are_empty_without_a_register(tmp_path):
    """The field still works; it simply offers nothing."""
    box, street, _, _, _ = _fields(tmp_path / "gibt-es-nicht.sqlite3")
    street.value = "Erstweg"

    box.update()

    assert box.suggestions == []


def test_the_suggestion_source_is_the_shared_lookup(address_register):
    """One mechanism, not a second search living in the widget."""
    box, street, _, _, _ = _fields(address_register)
    street.value = "Erstweg"
    box.update()

    assert box.suggestions == suggest_addresses("Erstweg", path=address_register)


def test_yes_writes_the_field_the_finding_is_about(address_register):
    """A postal-code finding once landed in the street field.

    An "everything that is not the locality is the street" branch invites
    exactly that, which is why the mapping is explicit.
    """
    site_id = _site(street="Drittweg", number="1", locality="Beispiel Dorf")
    with connection_scope() as connection:
        site = site_repo.get(connection, site_id)
        site.postal_code = "3048"
        site_repo.update(connection, site)

    issue = next(i for i in _issues() if i.object_id == site_id)
    apply_suggestion(issue)

    with connection_scope() as connection:
        corrected = site_repo.get(connection, site_id)
    assert corrected.postal_code == "3065"
    assert corrected.street == "Drittweg", "die Strasse war richtig und bleibt"


def test_yes_corrects_a_locality(address_register):
    """The ordinary case, so the mapping is checked in both directions."""
    site_id = _site(locality="Grossgemeinde")

    apply_suggestion(next(i for i in _issues() if i.object_id == site_id))

    with connection_scope() as connection:
        assert site_repo.get(connection, site_id).municipality == "Musterdorf"


def test_yes_does_nothing_when_there_is_nothing_to_offer(address_register):
    """A hint without a suggestion carries only a Nein, so Ja must be inert."""
    site_id = _site(street="Nirgendweg")
    issue = next(i for i in _issues() if i.object_id == site_id)
    assert issue.finding.suggestion == ""

    apply_suggestion(issue)

    with connection_scope() as connection:
        assert site_repo.get(connection, site_id).street == "Nirgendweg"
