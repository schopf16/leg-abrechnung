"""Tests that the drawer answers "where am I" and reads like the work.

The drawer is an accordion of rows: exactly one chapter is open, and the
open entry carries a grey bar across the full width. Both halves are
checked here, because each looks fine on screen while the other is broken --
a chapter that opens without the bar says the area but not the page, and a
bar inside a chapter that can be left open alongside three others says the
page but not the area.
"""

import pytest
from nicegui import Client, ui

from app.gui.navigation import NAV_GROUPS, page_frame


def _render(route: str, title: str) -> Client:
    """Render a page frame for one route.

    Args:
        route: The active route.
        title: The page title.

    Returns:
        The client holding the rendered frame.
    """
    client = Client(ui.page(f"/probe-nav{route.replace('/', '-')}")(lambda: None), request=None)
    with client:
        with page_frame(route, title):
            ui.label("Inhalt")
    return client


def _groups(client: Client) -> dict:
    """The navigation chapters, by their label.

    Args:
        client: The rendered client.

    Returns:
        `{label: element}`.
    """
    return {
        element._props.get("label"): element
        for element in client.elements.values()
        if element.__class__.__name__ == "Expansion"
    }


@pytest.mark.parametrize(
    "route, group",
    [
        ("/sites", "Stammdaten"),
        ("/onboardings", "Vorgänge"),
        ("/billing", "Abrechnung"),
        ("/statistics/energy", "Statistik"),
        ("/settings", "Einstellungen"),
    ],
)
def test_exactly_the_chapter_of_the_page_is_open(route, group):
    """One chapter open, and it is the one the page belongs to."""
    groups = _groups(_render(route, "Titel"))

    open_now = [label for label, element in groups.items() if element.value]

    assert open_now == [group]


def test_the_chapters_are_one_accordion():
    """Without Quasar's `group`, two chapters could stand open at once.

    That is the whole of draft C: the open chapter *is* the location, so it
    must not be possible to leave a second one open beside it.
    """
    groups = _groups(_render("/sites", "Standorte"))

    assert groups, "keine Kapitel gefunden"
    for label, element in groups.items():
        assert element._props.get("group") == "leg-nav", label


def test_the_open_entry_carries_the_grey_bar():
    """The chapter answers the area, the bar answers the page."""
    client = _render("/sites", "Standorte")

    marked = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Link" and "leg-nav-active" in element._classes
    ]

    assert len(marked) == 1
    assert marked[0].text == "Standorte"


def test_an_entry_is_a_row_and_not_a_hyperlink():
    """Blue and underlined reads as "this leaves the page"."""
    client = _render("/sites", "Standorte")

    links = [element for element in client.elements.values() if element.__class__.__name__ == "Link"]

    assert links
    for element in links:
        assert "leg-nav-item" in element._classes, element.text
        assert "text-primary" not in element._classes, element.text


def test_every_route_in_the_navigation_belongs_to_exactly_one_group():
    """Otherwise two chapters would open, or none.

    Checked against the table itself rather than a copy of it, so adding an
    entry twice fails here instead of looking odd on screen.
    """
    seen: dict[str, str] = {}
    for group_label, items in NAV_GROUPS:
        for route, _ in items:
            assert route not in seen, f"{route} steht in {seen.get(route)} und in {group_label}"
            seen[route] = group_label or "(ohne Gruppe)"

    assert len(seen) >= 20


def test_the_settings_page_is_not_called_stammdaten_any_more():
    """ "Stammdaten" is a chapter now, so the name cannot mean two things.

    The administrator looked for the master-data lists under that entry and
    found the LEG's sender address and the electricity price.
    """
    labels = {route: label for _, items in NAV_GROUPS for route, label in items}

    assert labels["/settings"] == "Allgemein"
    assert [group for group, _ in NAV_GROUPS].count("Stammdaten") == 1


def test_the_quarter_runs_in_the_order_the_chapter_lists_it():
    """Auswertungen is the control sheet read before the run, not after it."""
    billing = next(items for group, items in NAV_GROUPS if group == "Abrechnung")
    order = [label for _, label in billing]

    assert order.index("Auswertungen") < order.index("Rechnungslauf")
    assert order == ["Import", "Auswertungen", "Rechnungslauf", "Debitoren", "Mahnwesen"]
