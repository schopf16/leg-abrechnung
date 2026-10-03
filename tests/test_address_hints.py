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

**The question belongs at the field.** The first build asked it in a card
above the Standorte and Personen lists, where a line read as a name and
"Meinten Sie: Untere Zollgasse?" with no sight of which field was meant or
what stood in it. The administrator could not answer that, and was right.
The lists now only mark a record; the hint is rendered beside the value it
would replace, and the tests below check both halves.
"""

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain.address_check import (
    KIND_PERSON,
    KIND_SITE,
    address_signature,
    find_address_issues,
)
from app.domain.address_check import issue_ids
from app.domain.address_lookup import FIELD_LOCALITY, suggest_addresses
from app.gui.address_input import (
    DISMISS_ADDRESS,
    DISMISS_LOCALITY,
    SuggestionBox,
    store_dismissals,
)
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


# --- The marker on the lists ----------------------------------------------


def test_an_affected_record_is_marked(address_register):
    """The list says "look at this one" and nothing more.

    It cannot say more honestly: a row has no room to show which field is
    wrong and what stands in it, and a suggestion without its subject is
    unanswerable.
    """
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
    """Build the address inputs of a dialog, wired to a `SuggestionBox`.

    Args:
        register: Path of the test register.
        with_hints: Whether to attach the hint containers, as the real
            dialogs do.

    Returns:
        `(box, street, house_number, postal_code, locality)`.
    """
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
    """The list floats over the form, so it has to be dismissable.

    An inline list resized the dialog on every keystroke, which is exactly
    when the administrator is reading what they type.
    """
    box, street, _, _, _ = _fields(address_register)
    street.value = "Erstweg"
    box.update()
    assert box.suggestions

    box.hide()

    assert box.suggestions == []
    assert street.value == "Erstweg"


def test_a_postal_code_already_in_the_form_ranks_the_suggestions(address_register):
    """Typing a street with the postal code filled in offered six streets
    from other cantons above the one that fitted.

    Ranked rather than filtered: a street really can sit behind a different
    postal code, and that case is what `verify` reports -- hiding it would
    make the correction unreachable.
    """
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
    """The dialog collects it; saving writes it.

    After the save rather than inside it: a new record has no id while the
    dialog is open.
    """
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
