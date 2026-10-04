"""Tests for a couple held as one Person: two names, two addresses, one bill.

The model side is cheap to check and easy to get wrong in ways that reach a
real letter -- an address block that cannot be matched to a salutation, a
payment slip that refuses an over-long name. The click paths at the bottom
exist because a page rendering proves only that it builds: the "Nur
Genossenschafter" switch and the form's own save handler are driven for
real (see CLAUDE.md on the nicegui 3.16 upload handler).
"""

from datetime import date

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.models import cooperative_membership as coop_repo
from app.models import person as person_repo
from app.models.cooperative_membership import CooperativeMembership
from app.models.person import Person
from app.pdf.qr_bill_render import QR_NAME_MAX_LENGTH, qr_debtor_name, qr_debtor_name_note


def _couple(**overrides) -> Person:
    """Build a Person naming two people.

    Args:
        **overrides: Fields to override on the person.

    Returns:
        An unpersisted `Person` holding a couple.
    """
    fields = dict(
        id=None,
        salutation="Frau",
        company="",
        first_name="Anna",
        last_name="Muster",
        contact_email="anna@example.invalid",
        contact_phone="",
        billing_street="Fischrain",
        billing_house_number="68",
        billing_postal_code="3063",
        billing_city="Ittigen",
        billing_country="CH",
        iban="",
        customer_number=None,
        bkw_customer_number=None,
        paper_invoice=False,
        active=True,
        created_at="",
        second_salutation="Herr",
        second_first_name="Beat",
        second_last_name="Beispiel",
        second_contact_email="beat@example.invalid",
    )
    fields.update(overrides)
    return Person(**fields)


def test_the_address_block_has_one_line_per_person(db):
    """Each person on their own line, salutation in front of the name."""
    assert _couple().address_block_lines == ["Frau Anna Muster", "Herr Beat Beispiel"]


def test_a_single_person_keeps_one_line(db):
    """The change must not cost the ordinary case its shape."""
    single = _couple(second_salutation="", second_first_name="", second_last_name="")
    assert single.address_block_lines == ["Frau Anna Muster"]


def test_a_company_with_a_couple_as_contacts_lists_all_three_lines(db):
    """Company first, then the named people -- Swiss business-letter order."""
    person = _couple(company="Hauswartung AG")
    assert person.address_block_lines == [
        "Hauswartung AG",
        "Frau Anna Muster",
        "Herr Beat Beispiel",
    ]


def test_the_display_name_names_both(db):
    """The customer is the couple, so the customer's name is both names."""
    assert _couple().display_name == "Anna Muster und Beat Beispiel"


def test_a_second_salutation_alone_does_not_make_a_second_person(db):
    """Without a name there is nobody to address."""
    person = _couple(second_salutation="Herr", second_first_name="", second_last_name="")
    assert not person.has_second_person
    assert person.address_block_lines == ["Frau Anna Muster"]


def test_both_addresses_are_returned_in_order(db):
    """One message goes to both, so both have to be there."""
    assert _couple().contact_emails == ["anna@example.invalid", "beat@example.invalid"]


def test_one_address_twice_is_returned_once(db):
    """A shared mailbox must not be mailed twice."""
    person = _couple(second_contact_email="anna@example.invalid")
    assert person.contact_emails == ["anna@example.invalid"]


def test_no_address_at_all_is_an_empty_list(db):
    """`contact_emails` is what callers check before sending."""
    person = _couple(contact_email="", second_contact_email="")
    assert person.contact_emails == []


def test_a_couple_survives_a_database_round_trip(db):
    """Every new column is written by `create` and read by `from_row`."""
    person_id = person_repo.create(db, _couple(note="Zahlt per Dauerauftrag."))

    fetched = person_repo.get(db, person_id)
    assert fetched.second_first_name == "Beat"
    assert fetched.second_last_name == "Beispiel"
    assert fetched.second_salutation == "Herr"
    assert fetched.second_contact_email == "beat@example.invalid"
    assert fetched.note == "Zahlt per Dauerauftrag."
    assert fetched.deactivated_at is None

    fetched.second_last_name = "Beispiel-Muster"
    fetched.note = "Neu: per E-Banking."
    person_repo.update(db, fetched)
    again = person_repo.get(db, person_id)
    assert again.second_last_name == "Beispiel-Muster"
    assert again.note == "Neu: per E-Banking."


def test_deactivating_records_the_date_and_reactivating_clears_it(db):
    """ "Inaktiv" without a date was the complaint; both directions matter."""
    person_id = person_repo.create(db, _couple())

    person_repo.set_active(db, person_id, False)
    deactivated = person_repo.get(db, person_id)
    assert deactivated.active is False
    assert deactivated.deactivated_at == date.today()

    person_repo.set_active(db, person_id, True)
    reactivated = person_repo.get(db, person_id)
    assert reactivated.active is True
    assert reactivated.deactivated_at is None, "ein aktiver Mensch hat kein Austrittsdatum"


def test_a_person_deactivated_before_the_column_existed_has_no_date(db):
    """Migration 50 leaves the date NULL rather than inventing one.

    A made-up date would be printed on a list as though it were recorded.
    """
    person_id = person_repo.create(db, _couple())
    # Exactly what the old `set_active` did, and what migration 50 left behind.
    db.execute("UPDATE person SET active = 0, deactivated_at = NULL WHERE id = ?", (person_id,))
    db.commit()

    person = person_repo.get(db, person_id)
    assert person.active is False
    assert person.deactivated_at is None


# --- The QR-bill's 70-character name limit ------------------------------


def test_a_normal_couple_keeps_both_names_on_the_payment_part(db):
    """Nothing is shortened while it fits."""
    person = _couple()
    assert len(person.display_name) <= QR_NAME_MAX_LENGTH
    assert qr_debtor_name(person) == person.display_name
    assert qr_debtor_name_note(person) is None


def test_an_over_long_couple_name_falls_back_to_the_first_person(db):
    """An unusable document is worse than a shortened name on the slip.

    `qrbill` raises for a name over 70 characters, and that used to surface
    as "check the QR-IBAN and sender address" -- an error about something
    entirely unrelated, for an invoice that then did not exist at all.
    """
    person = _couple(
        first_name="Anna-Katharina",
        last_name="von Muster-Lindenberg",
        second_first_name="Beat-Christoph",
        second_last_name="Beispiel-Hofstetter",
    )
    assert len(person.display_name) > QR_NAME_MAX_LENGTH

    used = qr_debtor_name(person)
    assert used == "Anna-Katharina von Muster-Lindenberg"
    assert len(used) <= QR_NAME_MAX_LENGTH

    note = qr_debtor_name_note(person)
    assert note is not None
    assert used in note, "der Hinweis muss sagen, was stattdessen gedruckt wurde"


def test_the_full_name_still_appears_in_the_address_block(db):
    """Shortening affects the payment part only -- the letter names both."""
    person = _couple(
        first_name="Anna-Katharina",
        last_name="von Muster-Lindenberg",
        second_first_name="Beat-Christoph",
        second_last_name="Beispiel-Hofstetter",
    )
    assert person.address_block_lines == [
        "Frau Anna-Katharina von Muster-Lindenberg",
        "Herr Beat-Christoph Beispiel-Hofstetter",
    ]


def test_an_over_long_single_name_is_truncated_rather_than_dropped(db):
    """There is no first person to fall back to, so it is cut.

    A name is still better than no bill.
    """
    person = _couple(
        company="Ausserordentlich lange Liegenschaftsverwaltungs- und "
        "Immobilientreuhand Aktiengesellschaft Bern",
        first_name="",
        last_name="",
        second_first_name="",
        second_last_name="",
    )
    used = qr_debtor_name(person)
    assert len(used) == QR_NAME_MAX_LENGTH
    assert person.company.startswith(used)


# --- Click paths ------------------------------------------------------


def _probe(page_callable, route: str) -> Client:
    """Render one page function inside a throwaway NiceGUI client.

    Args:
        page_callable: Zero-argument callable rendering the page.
        route: A unique probe route, since each `ui.page` registers itself.

    Returns:
        The client, whose `elements` hold what was rendered.
    """
    client = Client(ui.page(route)(lambda: None), request=None)
    with client:
        page_callable()
    return client


def _labels(client: Client) -> list[str]:
    """Collect the text of every rendered label and badge.

    Badges as well as labels: the membership marker on a card is a
    `ui.badge`, and checking only labels silently missed it.

    Args:
        client: The client to read.

    Returns:
        The non-empty texts.
    """
    return [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ in ("Label", "Badge") and getattr(element, "text", None)
    ]


def _switch(client: Client, label: str):
    """Find one switch by its label.

    Args:
        client: The client to search.
        label: The switch's German label.

    Returns:
        The matching switch element.
    """
    matches = [
        element
        for element in client.elements.values()
        # The caption lives in the `label` prop, which is the half Quasar
        # makes clickable -- see `app.gui.filter_bar`.
        if element.__class__.__name__ == "Switch" and element._props.get("label") == label
    ]
    assert len(matches) == 1, f"Schalter {label!r} nicht eindeutig: {len(matches)}"
    return matches[0]


def _set_switch(switch, value: bool) -> None:
    """Flip a switch and fire its change handler, as a click would.

    Args:
        switch: The switch element.
        value: The new value.

    Returns:
        None.
    """
    assert switch._change_handlers, "der Schalter hat keinen Change-Handler -- er tut nichts"
    # Assigning `.value` is what NiceGUI itself does on a real click: the
    # setter runs `_handle_value_change`, which calls the registered
    # `on_value_change` handler -- here, the page's own `apply_filter`.
    switch.value = value


def test_the_persons_page_shows_a_couple_and_its_note():
    """Both names, both addresses and the internal note reach the card."""
    with connection_scope() as connection:
        person_repo.create(connection, _couple(note="Zahlt per Dauerauftrag."))

    client = _probe(_persons_page, "/probe-persons-couple")
    labels = _labels(client)

    assert any("Anna Muster und Beat Beispiel" in text for text in labels)
    assert "beat@example.invalid" in labels
    assert any("Dauerauftrag" in text for text in labels)


def test_the_cooperative_filter_actually_filters():
    """Pressing the switch has to change the list, not just exist.

    The filter is the members' list: whatever it leaves visible is what
    gets printed.
    """
    with connection_scope() as connection:
        member_id = person_repo.create(connection, _couple(last_name="Mitglied"))
        person_repo.create(
            connection,
            _couple(
                last_name="Kundin",
                second_first_name="",
                second_last_name="",
                second_contact_email="",
            ),
        )
        coop_repo.create(
            connection,
            CooperativeMembership(
                id=None,
                person_id=member_id,
                shares=7,
                valid_from=date(2020, 1, 1),
                valid_to=None,
                created_at="",
            ),
        )

    client = _probe(_persons_page, "/probe-persons-coop-filter")
    assert any("Kundin" in text for text in _labels(client)), "ungefiltert sind beide da"
    assert any("Genossenschafter (7 Anteile)" in text for text in _labels(client))

    _set_switch(_switch(client, "Nur Genossenschafter"), True)
    filtered = _labels(client)
    assert any("Mitglied" in text for text in filtered)
    assert not any("Kundin" in text for text in filtered), "die Nicht-Mitglieder müssen weg sein"


def test_the_detail_page_shows_the_membership_history_and_the_salutation():
    """The history is maintained here, so it has to be visible here."""
    with connection_scope() as connection:
        person_id = person_repo.create(connection, _couple())
        coop_repo.create(
            connection,
            CooperativeMembership(
                id=None,
                person_id=person_id,
                shares=5,
                valid_from=date(2024, 1, 1),
                valid_to=date(2025, 12, 31),
                created_at="",
            ),
        )
        coop_repo.create(
            connection,
            CooperativeMembership(
                id=None,
                person_id=person_id,
                shares=12,
                valid_from=date(2026, 1, 1),
                valid_to=None,
                created_at="",
            ),
        )

    client = _probe(lambda: _person_detail_page(person_id), "/probe-person-detail")
    labels = _labels(client)

    assert any("Guten Tag Frau Muster, guten Tag Herr Beispiel" in text for text in labels)
    assert any("Mitglied mit 12 Anteil(en)" in text for text in labels)
    assert any("5 Anteil(e)" in text for text in labels), "der frühere Zeitraum bleibt sichtbar"
    assert any("12 Anteil(e)" in text for text in labels)
    assert any("Zweite Person: Herr Beat Beispiel" in text for text in labels)


def _persons_page() -> None:
    """Render the persons list page.

    Returns:
        None.
    """
    from app.gui.pages import persons as persons_module

    persons_module.persons_page()


def _person_detail_page(person_id: int) -> None:
    """Render one person's detail page.

    Args:
        person_id: The person to show.

    Returns:
        None.
    """
    from app.gui.pages import persons as persons_module

    persons_module.person_detail_page(person_id)
