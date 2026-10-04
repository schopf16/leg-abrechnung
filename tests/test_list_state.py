"""What a list was showing survives a page change.

The loop the problem markers were built for -- see the triangle, open the
record, fix it, come back -- threw everything away on the last step: the
filter was off, the search box empty, the sort back to default and, since the
lists page, the position back on page one. With ninety-two rows that is the
difference between correcting a handful and hunting for them twice.
"""

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.gui.filter_bar import FilterBar
from app.gui.list_state import recall, remember
from app.gui.table_list import DEFAULT_PAGE_SIZE
from app.models import person as person_repo
from app.models.person import Person


def _person(last_name: str) -> int:
    """Create one person.

    Args:
        last_name: Surname, also what a search matches.

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


def _persons_page(probe: str) -> Client:
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


def _element(client: Client, kind: str, **props):
    """Find one element by class name and props.

    Args:
        client: The rendered client.
        kind: The element class name, e.g. "Switch".
        **props: Props that must match.

    Returns:
        The first match.
    """
    for element in client.elements.values():
        if element.__class__.__name__ != kind:
            continue
        if all(element._props.get(key) == value for key, value in props.items()):
            return element
    raise AssertionError(f"kein {kind} mit {props}")


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


def _table(client: Client):
    """The list's table.

    Args:
        client: The rendered client.

    Returns:
        The table element.
    """
    return next(element for element in client.elements.values() if element.__class__.__name__ == "Table")


def _change(element) -> None:
    """Fire an element's change handlers, as a click or a keystroke would.

    Args:
        element: The element whose value was just set.

    Returns:
        None.
    """
    for handler in element._change_handlers:
        handler(None)


def test_the_search_text_comes_back():
    """The administrator left to fix something, not to start over."""
    _person("Gesucht")
    _person("Andere")

    first = _persons_page("/probe-state-search-1")
    search = _search(first)
    search.value = "Gesucht"
    _change(search)

    second = _persons_page("/probe-state-search-2")

    assert _search(second).value == "Gesucht"
    assert [row["name"] for row in _table(second).rows] == ["Anna Gesucht"]


def test_a_filter_comes_back_switched_on():
    """Three switches on Personen, and re-setting them was the whole chore."""
    _person("Muster")

    first = _persons_page("/probe-state-filter-1")
    switch = _element(first, "Switch", label="Deaktivierte Personen anzeigen")
    switch.value = True
    _change(switch)

    second = _persons_page("/probe-state-filter-2")

    assert _element(second, "Switch", label="Deaktivierte Personen anzeigen").value is True


def test_the_sort_order_and_its_direction_come_back():
    """Both halves: `SortControl` carries the direction separately."""
    _person("Muster")
    remember("/persons", "sort", ("customer_number", True))

    client = _persons_page("/probe-state-sort")

    select = _element(client, "Select", label="Sortierung")
    assert select.value == "customer_number"
    arrow = _element(client, "Button", icon="arrow_downward")
    assert arrow is not None


def test_the_page_and_the_page_size_come_back():
    """Correcting a record on page four and returning to page one is the
    thing this answers."""
    _person("Muster")
    remember("/persons", "pagination", {"page": 2, "rowsPerPage": 30})

    client = _persons_page("/probe-state-page")

    pagination = _table(client)._props["pagination"]
    assert pagination["page"] == 2
    assert pagination["rowsPerPage"] == 30


def test_a_list_that_was_never_visited_starts_at_its_defaults():
    """Nothing remembered is the normal case, not an empty-state bug."""
    _person("Muster")

    client = _persons_page("/probe-state-default")

    assert _search(client).value == ""
    assert _table(client)._props["pagination"]["rowsPerPage"] == DEFAULT_PAGE_SIZE


def test_a_bar_without_a_route_remembers_nothing():
    """A dialog or a detail sub-table must not write into a list's state."""
    client = Client(ui.page("/probe-state-routeless")(lambda: None), request=None)
    with client:
        bar = FilterBar()
        field = bar.search("Name")
        field.value = "egal"
        _change(field)

    assert recall("", "search") is None


def test_each_list_remembers_its_own() -> None:
    """Two lists, two states: the store is keyed by route."""
    remember("/persons", "search", "Muster")

    assert recall("/sites", "search") is None
    assert recall("/persons", "search") == "Muster"
