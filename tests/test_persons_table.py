"""The Personen list as a paged table."""

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.gui.table_list import DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS
from app.models import person as person_repo
from app.models.person import Person


def _person(last_name: str, *, street: str = "Erstweg", city: str = "Musterdorf") -> int:
    """Create one person."""
    with connection_scope() as connection:
        return person_repo.create(
            connection,
            Person(
                id=None,
                salutation="",
                company="",
                first_name="Anna",
                last_name=last_name,
                contact_email="",
                contact_phone="",
                billing_street=street,
                billing_house_number="4",
                billing_postal_code="3048",
                billing_city=city,
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


def _page(probe: str) -> Client:
    """Render the Personen list."""
    from app.gui.pages import persons as persons_module

    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        persons_module.persons_page()
    return client


def _table(client: Client):
    """The list's table."""
    return next(element for element in client.elements.values() if element.__class__.__name__ == "Table")


def _search(client: Client):
    """The list's search field."""
    return next(
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Input" and element.label == "Suche"
    )


def test_the_list_shows_the_three_columns_and_the_actions():
    """The administrator's own cut, so it is pinned rather than assumed."""
    _person("Muster")

    columns = [column["label"] for column in _table(_page("/probe-table-columns")).columns]

    assert columns == ["Kunden-Nr.", "Name", "Adresse", ""]


def test_the_table_pages_rather_than_growing_without_end():
    """Fifty by default, the administrator's number."""
    _person("Muster")

    table = _table(_page("/probe-table-paging"))

    assert table._props["pagination"]["rowsPerPage"] == DEFAULT_PAGE_SIZE
    assert DEFAULT_PAGE_SIZE == 50
    assert table._props["hide-pagination"] is False


def test_the_page_size_can_be_changed_from_the_list():
    """ "irgendwo sollte vielleicht ein drop-down sein wieviele personen pro liste angezeigt werden?" --..."""
    _person("Muster")

    table = _table(_page("/probe-table-page-sizes"))

    # A list, not a string: passed through `props()` Quasar received the
    # literal text and the select had nothing to offer, which is how the
    # administrator met it -- "die auswahl von 50 kann ich nicht erweitern".
    assert table._props["rows-per-page-options"] == PAGE_SIZE_OPTIONS
    assert isinstance(table._props["rows-per-page-options"], list)
    assert 0 in PAGE_SIZE_OPTIONS, "0 ist „alle“ -- eine kurze Liste soll nicht blättern müssen"
    assert table._props["rows-per-page-label"] == "Zeilen pro Seite"


def test_the_search_runs_over_everything_and_not_over_the_page():
    """The requirement in the administrator's words: "die suche / filter muss über alle gehen und nicht..."""
    for index in range(DEFAULT_PAGE_SIZE + 5):
        _person(f"Aaa{index:03d}")
    _person("Zyz")

    client = _page("/probe-table-search-all")
    table = _table(client)
    assert len(table.rows) == DEFAULT_PAGE_SIZE + 6, "die Tabelle bekommt alles"

    search = _search(client)
    search.value = "Zyz"
    for handler in search._change_handlers:
        handler(None)

    assert [row["name"] for row in table.rows] == ["Anna Zyz"]


def test_the_printout_holds_every_filtered_row_not_just_the_page():
    """A printout is read away from the screen; fifty of ninety-two rows would be wrong without saying..."""
    for index in range(DEFAULT_PAGE_SIZE + 5):
        _person(f"Aaa{index:03d}")

    client = _page("/probe-table-print-all")
    printed = next(
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Button" and "Drucken" in (element.text or "")
    )

    assert printed is not None
    assert len(_table(client).rows) == DEFAULT_PAGE_SIZE + 5


def test_a_deactivated_person_says_so_in_the_name_column():
    """There is no status column: it would be empty for nearly everyone."""
    person_id = _person("Weggezogen")
    with connection_scope() as connection:
        person_repo.set_active(connection, person_id, False)

    client = _page("/probe-table-inactive")
    switch = next(
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Switch"
        and element._props.get("label") == "Deaktivierte Personen anzeigen"
    )
    switch.value = True
    for handler in switch._change_handlers:
        handler(None)

    names = [row["name"] for row in _table(client).rows]
    assert any("Weggezogen" in name and "·" in name for name in names), names


def test_the_address_column_reads_as_one_address():
    """Street, number, postal code and locality, in one cell."""
    _person("Muster", street="Erstweg", city="Musterdorf")

    rows = _table(_page("/probe-table-address")).rows

    assert rows[0]["address"] == "Erstweg 4, 3048 Musterdorf"


def test_the_cells_can_be_marked_and_copied():
    """Quasar renders a table inside `.non-selectable`."""
    _person("Muster")

    table = _table(_page("/probe-table-selectable"))

    assert "leg-selectable" in table._classes


def test_the_page_shell_allows_the_selection_the_table_asks_for():
    """The class is nothing without the rule, and they live apart."""
    from nicegui import Client

    from app.gui.navigation import page_frame

    client = Client(ui.page("/probe-table-selectable-style")(lambda: None), request=None)
    with client:
        with page_frame("/persons", "Personen"):
            ui.label("Inhalt")

    styles = "".join(
        str(element._props.get("innerHTML", ""))
        for element in client.elements.values()
        if element.__class__.__name__ == "Html"
    )
    head = "".join(client.head_html)
    assert "leg-selectable" in head + styles
    assert "user-select: text !important" in head + styles
