"""Tests that the drawer says which part of the app is open.

The entry itself was already bold, but the chapter it sits in was not, so
the drawer never answered "where am I". Expanding the right group is not the
same as marking it: three groups can be open at once if the administrator
opened them.
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
    """The navigation groups, by their label.

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
        ("/sites", "Verwaltung"),
        ("/billing", "Abrechnung"),
        ("/statistics/energy", "Statistik"),
        ("/settings", "Einstellungen"),
    ],
)
def test_the_open_chapter_is_marked(route, group):
    """One group carries the mark, and it is the one the page belongs to."""
    groups = _groups(_render(route, "Titel"))

    assert "leg-nav-open-group" in groups[group]._classes, group
    for label, element in groups.items():
        if label != group:
            assert "leg-nav-open-group" not in element._classes, label


def test_every_route_in_the_navigation_belongs_to_exactly_one_group():
    """Otherwise two chapters would be marked, or none.

    Checked against the table itself rather than a copy of it, so adding an
    entry twice fails here instead of looking odd on screen.
    """
    seen: dict[str, str] = {}
    for group_label, items in NAV_GROUPS:
        for route, _ in items:
            assert route not in seen, f"{route} steht in {seen.get(route)} und in {group_label}"
            seen[route] = group_label or "(ohne Gruppe)"

    assert len(seen) >= 20


def test_the_open_entry_is_still_marked_too():
    """The chapter answers "where am I", the entry answers "which page"."""
    client = _render("/sites", "Standorte")

    links = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Link" and "leg-nav-active" in element._classes
    ]

    assert len(links) == 1
