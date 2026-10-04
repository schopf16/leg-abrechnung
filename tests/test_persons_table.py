"""The Personen list as a paged table.

Paging the wrong collection is the obvious way to build this and it looks
right until the list is longer than one page: the search would only find
what is already on screen, and the printout would hold fifty rows instead of
the ninety-two the filter left. Both are pinned here.

The move from cards to a table was a performance fix the administrator
asked for -- 92 cards of 23 interface elements each, 2'108 in all, against
one table whose rows are data -- and a trade: "wichtig wäre mir sicher
kundennummer, name vielleicht noch adresse? alles andere dann hinter auge".
"""

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.gui.table_list import DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS
from app.models import person as person_repo
from app.models.person import Person


def _person(last_name: str, *, street: str = "Erstweg", city: str = "Musterdorf") -> int:
    """Create one person.

    Args:
        last_name: Surname, also what the tests search for.
        street: Billing street.
        city: Billing locality.

    Returns:
        The new id.
    """
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
    """Render the Personen list.

    Args:
        probe: A unique probe route -- every `ui.page` registers itself.

    Returns:
        The client.
    """
    from app.gui.pages import persons as persons_module

    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        persons_module.persons_page()
    return client


def _table(client: Client):
    """The list's table.

    Args:
        client: The rendered client.

    Returns:
        The table element.
    """
    return next(element for element in client.elements.values() if element.__class__.__name__ == "Table")


def _search(client: Client):
    """The list's search field.

    Args:
        client: The rendered client.

    Returns:
        The input element.
    """
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
    """ "irgendwo sollte vielleicht ein drop-down sein wieviele personen pro
    liste angezeigt werden?" -- it is Quasar's own, in the table's footer."""
    _person("Muster")

    table = _table(_page("/probe-table-page-sizes"))

    assert table._props["rows-per-page-options"] == str(PAGE_SIZE_OPTIONS)
    assert 0 in PAGE_SIZE_OPTIONS, "0 ist „alle“ -- eine kurze Liste soll nicht blättern müssen"
    assert table._props["rows-per-page-label"] == "Zeilen pro Seite"


def test_the_search_runs_over_everything_and_not_over_the_page():
    """The requirement in the administrator's words: "die suche / filter muss
    über alle gehen und nicht nur auf das was aktuell angezeigt wird".

    More people than fit on one page, and the one searched for is last in
    the sort order -- so a search over the visible page would find nothing.
    """
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
    """A printout is read away from the screen; fifty of ninety-two rows
    would be wrong without saying so."""
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
    """There is no status column: it would be empty for nearly everyone.

    The information is not dropped, though -- a list that silently showed a
    deactivated person as an ordinary one would be worse than the card it
    replaced.
    """
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
