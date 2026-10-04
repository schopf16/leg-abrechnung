"""How many entries a list shows, and what it offers when it shows none.

A table says the count by itself -- Quasar prints "1-50 von 92" -- so these
are about the card lists, which said nothing at all. The Debitoren page could
be filtered down to nine of ninety-two with no sign that it had been.
"""

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.gui.filter_bar import FilterBar
from app.gui.list_footer import render_count, render_empty


def _texts(client: Client) -> list[str]:
    """Every non-empty label on a rendered page.

    Args:
        client: The rendered client.

    Returns:
        The texts.
    """
    return [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", "")
    ]


def _render(probe: str, build) -> Client:
    """Render one snippet.

    Args:
        probe: A unique probe route -- every `ui.page` registers itself.
        build: Zero-argument callable drawing into the client.

    Returns:
        The client.
    """
    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        build()
    return client


def test_an_unfiltered_list_states_its_size():
    """ "92 Personen", not "92 von 92 Personen"."""
    client = _render("/probe-footer-all", lambda: render_count(visible=92, total=92, noun="Personen"))

    assert "92 Personen" in _texts(client)


def test_a_filtered_list_says_how_much_it_is_hiding():
    """The number that was missing: nine on screen out of ninety-two."""
    client = _render("/probe-footer-some", lambda: render_count(visible=9, total=92, noun="Debitoren"))

    assert "9 von 92 Debitoren" in _texts(client)


def test_an_empty_list_can_be_empty_without_a_suggestion():
    """An empty Mahnwesen worklist is good news and needs no way out."""
    client = _render("/probe-footer-empty", lambda: render_empty("Noch keine Mahnung versendet."))

    assert "Noch keine Mahnung versendet." in _texts(client)
    buttons = [e for e in client.elements.values() if e.__class__.__name__ == "Button"]
    assert buttons == []


def test_an_empty_list_offers_the_way_out_when_there_is_one():
    """ "Keine passenden Austritte." is a statement; the next question is
    what to do about it."""
    pressed: list[bool] = []
    client = _render(
        "/probe-footer-action",
        lambda: render_empty(
            "Keine passenden Austritte.",
            action_label="Filter zurücksetzen",
            on_action=lambda: pressed.append(True),
        ),
    )

    button = next(e for e in client.elements.values() if e.__class__.__name__ == "Button")
    assert button.text == "Filter zurücksetzen"

    with client:
        handler = next(
            listener.handler for listener in button._event_listeners.values() if listener.type == "click"
        )
        handler(None)

    assert pressed == [True]


def test_the_bar_knows_whether_anything_is_filtered():
    """What decides whether the way out is worth offering.

    With nothing filtered, "Filter zurücksetzen" would be a button that does
    nothing and the list is simply empty.
    """
    client = Client(ui.page("/probe-footer-is-filtering")(lambda: None), request=None)
    with client:
        bar = FilterBar()
        search = bar.search("Name")
        switch = bar.filter("Nur Beispiel")

    assert bar.is_filtering() is False

    search.value = "Muster"
    assert bar.is_filtering() is True

    search.value = ""
    switch.value = True
    assert bar.is_filtering() is True


def test_resetting_puts_every_control_back_and_refreshes_once():
    """Five controls firing five refreshes is five rebuilds of the list."""
    refreshes: list[bool] = []
    client = Client(ui.page("/probe-footer-reset")(lambda: None), request=None)
    with client:
        bar = FilterBar()
        search = bar.search("Name")
        switch = bar.filter("Nur Beispiel")

    search.value = "Muster"
    switch.value = True

    bar.reset(lambda: refreshes.append(True))

    assert search.value == ""
    assert switch.value is False
    assert bar.is_filtering() is False
    assert refreshes == [True], "genau ein Neuaufbau"


def test_a_real_card_list_counts_what_it_shows():
    """Driven through the page, because the helper being right proves
    nothing about anybody calling it."""
    from datetime import date

    from app.gui.pages import offboardings as offboardings_module
    from app.models import person as person_repo
    from app.models import person_offboarding as offboarding_repo
    from app.models.person import Person

    with connection_scope() as connection:
        person_id = person_repo.create(
            connection,
            Person(
                id=None,
                salutation="",
                company="",
                first_name="Anna",
                last_name="Muster",
                contact_email="",
                contact_phone="",
                billing_street="Erstweg",
                billing_house_number="4",
                billing_postal_code="3048",
                billing_city="Musterdorf",
                billing_country="CH",
                iban="",
                paper_invoice=False,
                note="",
                customer_number=None,
                bkw_customer_number=None,
                active=True,
                created_at="",
            ),
        )
        offboarding_repo.start_for_person(connection, person_id, reason="voluntary", decided_at=date.today())

    client = Client(ui.page("/probe-footer-real")(lambda: None), request=None)
    with client:
        offboardings_module.offboardings_page()

    assert any("1 Austritte" in text for text in _texts(client)), _texts(client)
