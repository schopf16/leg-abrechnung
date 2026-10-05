"""Tests for the address hints: what is flagged, and what a click does."""

import pytest
from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain.address_check import (
    KIND_PERSON,
    KIND_SITE,
    address_signature,
    find_address_issues,
)
from app.domain.address_check import issue_ids
from app.domain.address_lookup import FIELD_HOUSE_NUMBER, FIELD_LOCALITY, suggest_addresses
from app.gui.address_input import (
    DISMISS_ADDRESS,
    DISMISS_LOCALITY,
    SuggestionBox,
    store_dismissals,
)
from app.gui.keyboard import layers
from app.models import person as person_repo
from app.models import site as site_repo
from app.models.person import Person
from app.models.site import Site


def _site(street: str = "Erstweg", number: str = "4", locality: str = "Musterdorf") -> int:
    """Create a site through `connection_scope`."""
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
    """Create a person with a billing address."""
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
    """Collect the current issues."""
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
    """Reversed from the first design on purpose."""
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
    """The reason the confirmed *value* is stored rather than a flag."""
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
    """`update` must not touch the confirmation columns."""
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


# --- The marker on the lists ----------------------------------------------


def test_an_affected_record_is_marked(address_register):
    """The list says "look at this one" and nothing more."""
    bad = _site(locality="Grossgemeinde")
    good = _site()

    with connection_scope() as connection:
        marked = issue_ids(connection, KIND_SITE)

    assert bad in marked
    assert good not in marked


def test_persons_are_marked_separately_from_sites(address_register):
    """Each list asks only about its own records."""
    site_id = _site(locality="Grossgemeinde")
    person_id = _person(street="Nirgendweg")

    with connection_scope() as connection:
        assert issue_ids(connection, KIND_SITE) == {site_id}
        assert issue_ids(connection, KIND_PERSON) == {person_id}


def test_nothing_is_marked_without_a_register():
    """No register, no claim about anybody's address."""
    _site(locality="Grossgemeinde")

    with connection_scope() as connection:
        assert issue_ids(connection, KIND_SITE) == set()


# --- The dialog: suggestions and hints at the field ------------------------


def _fields(register, *, with_hints: bool = False):
    """Build the address inputs of a dialog, wired to a `SuggestionBox`."""
    client = Client(ui.page("/probe-address-input")(lambda: None), request=None)
    with client:
        street = ui.input("Adresse")
        house_number = ui.input("Hausnummer")
        street_hint = ui.column() if with_hints else None
        postal_code = ui.input("PLZ")
        locality = ui.input("Ort")
        locality_hint = ui.column() if with_hints else None
        box = SuggestionBox(
            street,
            postal_code,
            locality,
            house_number,
            street_hint=street_hint,
            locality_hint=locality_hint,
            path=register,
        )
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
    """The other half, and the reason this is an input and not a select."""
    box, street, house_number, postal_code, locality = _fields(address_register)
    street.value = "Eigenerweg"
    house_number.value = "77"

    box.update()

    assert (street.value, house_number.value) == ("Eigenerweg", "77")
    assert (postal_code.value, locality.value) in ((None, None), ("", ""))


def test_escape_clears_the_list_without_touching_the_text(address_register):
    """The list floats over the form, so it has to be dismissable."""
    box, street, _, _, _ = _fields(address_register)
    street.value = "Erstweg"
    box.update()
    assert box.suggestions

    box.hide()

    assert box.suggestions == []
    assert street.value == "Erstweg"


def test_a_postal_code_already_in_the_form_ranks_the_suggestions(address_register):
    """Typing a street with the postal code filled in offered six streets from other cantons above the..."""
    box, street, _, postal_code, _ = _fields(address_register)
    postal_code.value = "3065"
    street.value = "Drittweg"

    box.update()

    assert box.suggestions[0].postal_code == "3065"


def test_a_locality_suggestion_leaves_the_street_alone(address_register):
    """Picking "3048 Musterdorf" must not wipe a street already typed."""
    box, street, _, postal_code, locality = _fields(address_register)
    street.value = "Eigenerweg"
    postal_code.value = "3048"
    box.update_locality(postal_code)

    box.apply(box.suggestions[0])

    assert street.value == "Eigenerweg"
    assert (postal_code.value, locality.value) == ("3048", "Musterdorf")


def test_the_hint_appears_for_the_value_in_the_field(address_register):
    """Beside the value it would replace, which is the whole correction."""
    box, street, house_number, postal_code, locality = _fields(address_register, with_hints=True)
    street.value, house_number.value = "Erstweg", "4"
    postal_code.value, locality.value = "3048", "Grossgemeinde"

    findings = box.findings()

    assert [(f.field, f.suggestion) for f in findings] == [(FIELD_LOCALITY, "Musterdorf")]


def test_yes_writes_the_field_the_finding_is_about(address_register):
    """A postal-code finding once landed in the street field."""
    box, street, house_number, postal_code, locality = _fields(address_register)
    street.value, house_number.value = "Drittweg", "1"
    postal_code.value, locality.value = "3048", "Musterdorf"
    finding = box.findings()[0]

    box.accept(finding)

    assert postal_code.value == "3065"
    assert street.value == "Drittweg", "die Strasse war richtig und bleibt"


def test_yes_corrects_a_locality(address_register):
    """The ordinary case, so the mapping is checked both ways."""
    box, street, house_number, postal_code, locality = _fields(address_register)
    street.value, house_number.value = "Erstweg", "4"
    postal_code.value, locality.value = "3048", "Grossgemeinde"

    box.accept(box.findings()[0])

    assert locality.value == "Musterdorf"


def test_no_silences_the_hint_for_that_exact_text(address_register):
    """One click has to settle it while the dialog is open."""
    box, street, house_number, postal_code, locality = _fields(address_register)
    street.value, house_number.value = "Postfach", ""
    postal_code.value, locality.value = "3048", "Musterdorf"

    box.dismiss(box.findings()[0])

    assert box.findings() == []
    assert box.dismissals[DISMISS_ADDRESS] == address_signature("Postfach", "", "3048")


def test_changing_the_text_after_a_no_asks_again(address_register):
    """The dismissal is the value, so it expires when the value does."""
    box, street, house_number, postal_code, locality = _fields(address_register)
    street.value, house_number.value = "Postfach", ""
    postal_code.value, locality.value = "3048", "Musterdorf"
    box.dismiss(box.findings()[0])
    assert box.findings() == []

    street.value = "Anderswegli"

    assert [f.field for f in box.findings()] == ["street"]


def test_a_dismissal_reaches_the_database_on_save(address_register):
    """The dialog collects it; saving writes it."""
    site_id = _site(street="Postfach")
    box, street, house_number, postal_code, locality = _fields(address_register)
    street.value, house_number.value = "Postfach", "4"
    postal_code.value, locality.value = "3048", "Musterdorf"
    box.dismiss(box.findings()[0])

    with connection_scope() as connection:
        store_dismissals(connection, box, site_id, "site")

    assert _issues() == []


def test_a_locality_dismissal_writes_the_locality_column(address_register):
    """Two columns, because the two findings are independent."""
    site_id = _site(locality="Grossgemeinde")
    box, street, house_number, postal_code, locality = _fields(address_register)
    street.value, house_number.value = "Erstweg", "4"
    postal_code.value, locality.value = "3048", "Grossgemeinde"
    box.dismiss(box.findings()[0])
    assert DISMISS_LOCALITY in box.dismissals

    with connection_scope() as connection:
        store_dismissals(connection, box, site_id, "site")
        assert site_repo.get(connection, site_id).locality_confirmed == "Grossgemeinde"

    assert _issues() == []


def test_suggestions_and_hints_are_empty_without_a_register(tmp_path):
    """The fields still work; they simply offer nothing."""
    box, street, _, _, _ = _fields(tmp_path / "gibt-es-nicht.sqlite3", with_hints=True)
    street.value = "Erstweg"

    box.update()

    assert box.suggestions == []
    assert box.findings() == []


def test_the_suggestion_source_is_the_shared_lookup(address_register):
    """One mechanism, not a second search living in the widget."""
    box, street, _, postal_code, _ = _fields(address_register)
    street.value = "Erstweg"
    box.update()

    assert box.suggestions == suggest_addresses("Erstweg", path=address_register, postal_code="")


@pytest.mark.parametrize(
    "street, number, postal_code, locality, field_index, expected",
    [
        # (which input must change, what it must become)
        ("Erstweg", "4a", "3048", "Musterdorf", 1, "4"),
        ("Drittweg", "1", "3048", "Musterdorf", 2, "3065"),
        ("Erstweg", "4", "3048", "Grossgemeinde", 3, "Musterdorf"),
    ],
)
def test_yes_writes_only_the_field_the_finding_is_about(
    address_register, street, number, postal_code, locality, field_index, expected
):
    """Twice now a suggestion has landed in the wrong field."""
    box, *fields = _fields(address_register)
    fields[0].value, fields[1].value = street, number
    fields[2].value, fields[3].value = postal_code, locality
    before = [f.value for f in fields]

    box.accept(box.findings()[0])

    after = [f.value for f in fields]
    assert after[field_index] == expected
    for index, (was, now) in enumerate(zip(before, after)):
        if index != field_index:
            assert now == was, f"Feld {index} wurde mitgeändert: {was!r} -> {now!r}"


def test_yes_leaves_everything_alone_when_the_field_is_unknown(address_register):
    """A finding this box cannot place must not be written somewhere plausible."""
    from app.domain.address_lookup import AddressFinding

    box, *fields = _fields(address_register)
    fields[0].value, fields[1].value = "Erstweg", "4"
    fields[2].value, fields[3].value = "3048", "Musterdorf"
    before = [f.value for f in fields]

    box.accept(AddressFinding("etwas_neues", "x", "y"))

    assert [f.value for f in fields] == before


def test_a_form_without_a_house_number_field_is_not_written_into(address_register):
    """The settings page keeps street and number in one field."""
    from app.domain.address_lookup import AddressFinding

    client = Client(ui.page("/probe-no-number-field")(lambda: None), request=None)
    with client:
        street = ui.input("Strasse")
        postal_code = ui.input("PLZ")
        locality = ui.input("Ort")
        box = SuggestionBox(street, postal_code, locality, path=address_register)
    street.value = "Erstweg 4"

    box.accept(AddressFinding(FIELD_HOUSE_NUMBER, "4a", "4"))

    assert street.value == "Erstweg 4"


# --- The list has a keyboard ----------------------------------------------
#
# Reported from use: "beim enter in einem feld mit vorschläge öffnet die
# vorschläge, ich kann dann aber mit pfeil hoch runter nicht durchscrollen
# oder mit enter auswählen". No key is bound to an element any more: the
# list floats with `no-focus` and a Quasar dialog renders its card in a
# portal, so an element binding depends on both the focus and the event
# bubbling out of it. `app.gui.keyboard` owns the keys and the list takes
# them while it is open.
#
# Driven through the postal code, because that is the field whose query
# returns more than one entry from the test register.


def _open_locality_list(register):
    """Type a postal code that matches both localities."""
    box, street, _, postal_code, locality = _fields(register)
    postal_code.value = "30"
    box.update_locality(postal_code)
    assert len(box.suggestions) > 1, box.suggestions
    return box, street, postal_code, locality


def test_the_open_list_is_the_innermost_thing_and_owns_the_keys(address_register):
    """It takes the keys when it opens and gives them back when it closes."""
    box, _, _, _ = _open_locality_list(address_register)

    assert layers()[-1] is box._layer

    box.hide()

    assert box._layer not in layers()


def test_the_arrows_walk_the_open_list(address_register, press):
    """Down lands on the first entry, up from nothing on the last."""
    box, _, _, _ = _open_locality_list(address_register)

    press("ArrowDown")
    assert box._highlight == 0

    press("ArrowDown")
    assert box._highlight == 1

    box._highlight = -1
    press("ArrowUp")
    assert box._highlight == len(box.suggestions) - 1


def test_left_and_right_walk_it_too(address_register, press):
    """All four arrows mark, which is what the administrator asked for."""
    box, _, _, _ = _open_locality_list(address_register)

    press("ArrowRight")
    assert box._highlight == 0

    press("ArrowLeft")
    assert box._highlight == len(box.suggestions) - 1


def test_the_arrows_wrap_rather_than_stopping(address_register, press):
    """A short list is walked round faster than back."""
    box, _, _, _ = _open_locality_list(address_register)
    box._highlight = len(box.suggestions) - 1

    press("ArrowDown")

    assert box._highlight == 0


def test_enter_takes_the_entry_the_arrows_reached(address_register, press):
    """The other half of the report: Enter did nothing."""
    box, _, postal_code, locality = _open_locality_list(address_register)
    press("ArrowDown")
    chosen = box.suggestions[0]

    press("Enter")

    assert postal_code.value == chosen.postal_code
    assert locality.value == chosen.locality
    assert box.suggestions == []


def test_escape_pushes_the_list_aside_and_keeps_the_text(address_register, press):
    """One press dismisses the list; the form behind it stays open."""
    box, _, postal_code, _ = _open_locality_list(address_register)

    press("Escape")

    assert box.suggestions == []
    assert postal_code.value == "30"


def test_enter_takes_nothing_that_was_not_stepped_onto(address_register, press):
    """Deliberate, and the project has already paid for the lesson."""
    box, _, postal_code, locality = _open_locality_list(address_register)
    assert box._highlight == -1

    press("Enter")

    assert postal_code.value == "30", "nichts darf übernommen worden sein"
    assert not locality.value


def test_a_new_search_forgets_where_the_arrows_were(address_register, press):
    """Otherwise the index points into the previous set of suggestions."""
    box, street, _, _ = _open_locality_list(address_register)
    press("ArrowDown")
    assert box._highlight == 0

    street.value = "Erstweg"
    box.update()

    assert box._highlight == -1
