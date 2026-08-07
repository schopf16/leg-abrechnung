"""Shared page shell: header, side navigation and page-content container.

Every page module calls :func:`page_frame` at the top of its ``@ui.page``
handler to get a consistently styled window with the same navigation on
every screen.
"""

from contextlib import contextmanager
from typing import Iterator

from nicegui import ui

#: (route, label) pairs shown in the side navigation, in display order.
NAV_ITEMS = [
    ("/", "Übersicht"),
    ("/personen", "Personen"),
    ("/messpunkte", "Messpunkte"),
    ("/standorte", "Standorte"),
    ("/legs", "LEGs"),
    ("/zuordnungen", "Zuordnungen"),
    ("/import", "Import"),
    ("/abrechnung", "Abrechnung"),
    ("/auswertungen", "Auswertungen"),
    ("/einstellungen", "Einstellungen"),
    ("/backup", "Backup"),
]


@contextmanager
def page_frame(active_route: str, title: str) -> Iterator[None]:
    """Render the common header and drawer, yielding a container for content.

    Args:
        active_route: The route path of the currently shown page, used to
            highlight the matching navigation entry.
        title: Page title shown in the header bar.

    Yields:
        None. Code inside the ``with`` block is placed in the page's main
        content area.
    """
    ui.add_head_html(
        "<style>"
        ".leg-nav-active { font-weight: 700; }"
        "</style>"
    )
    ui.page_title(f"LEG-Abrechnung – {title}")

    with ui.header().classes("items-center justify-between bg-primary text-white"):
        ui.label("LEG-Abrechnung").classes("text-lg font-bold")
        ui.label(title).classes("text-md")

    with ui.left_drawer(fixed=True).classes("bg-grey-1").props("width=220"):
        for route, label in NAV_ITEMS:
            classes = "w-full" + (" leg-nav-active text-primary" if route == active_route else "")
            ui.link(label, route).classes(classes).style("display:block; padding:6px 0;")

    with ui.column().classes("w-full max-w-5xl mx-auto p-4") as content:
        yield content
