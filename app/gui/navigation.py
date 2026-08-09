"""Shared page shell: header, side navigation and page-content container.

Every page module calls :func:`page_frame` at the top of its ``@ui.page``
handler to get a consistently styled window with the same navigation on
every screen.
"""

from contextlib import contextmanager
from typing import Iterator, Optional

from nicegui import ui

#: Side navigation, grouped by where each page sits in the actual
#: workflow (master data -> billing -> analysis -> configuration) rather
#: than the chronological order pages were added in. Each entry is
#: ``(group_label, [(route, label), ...])``; ``group_label`` of ``None``
#: renders its items without a collapsible section (used for the single
#: top-level "Übersicht" entry).
NAV_GROUPS: list[tuple[Optional[str], list[tuple[str, str]]]] = [
    (None, [("/", "Übersicht")]),
    (
        "Verwaltung",
        [
            ("/trafokreise", "Trafokreise"),
            ("/standorte", "Standorte"),
            ("/legs", "LEGs"),
            ("/messpunkte", "Messpunkte"),
            ("/personen", "Personen"),
            ("/zuordnungen", "Zuordnungen"),
        ],
    ),
    (
        "Abrechnung",
        [
            ("/import", "Import"),
            ("/abrechnung", "Rechnungslauf"),
            ("/auswertungen", "Auswertungen"),
        ],
    ),
    ("Statistik", [("/statistik", "Statistik")]),
    (
        "Einstellungen",
        [
            ("/einstellungen", "Stammdaten"),
            ("/backup", "Backup"),
        ],
    ),
]


def _nav_link(route: str, label: str, active_route: str, *, indent: bool) -> None:
    """Render one navigation link, highlighted if it matches the current page.

    Args:
        route: Target route path.
        label: Visible link text.
        active_route: Route path of the currently shown page.
        indent: Whether to indent the link (used for links inside a
            collapsible group, as opposed to the top-level "Übersicht").

    Returns:
        None.
    """
    classes = "w-full" + (" leg-nav-active text-primary" if route == active_route else "")
    padding = "6px 12px 6px 28px" if indent else "6px 12px"
    ui.link(label, route).classes(classes).style(f"display:block; padding:{padding};")


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
        ".leg-nav-group .q-item { padding: 6px 12px; min-height: 0; }"
        ".leg-nav-group .q-item__label { font-size: 13px; font-weight: 600; }"
        "</style>"
    )
    ui.page_title(f"LEG-Abrechnung – {title}")

    with ui.header().classes("items-center justify-between bg-primary text-white"):
        ui.label("LEG-Abrechnung").classes("text-lg font-bold")
        ui.label(title).classes("text-md")

    with ui.left_drawer(fixed=True).classes("bg-grey-1 q-pa-none").props("width=240"):
        for group_label, items in NAV_GROUPS:
            if group_label is None:
                for route, label in items:
                    _nav_link(route, label, active_route, indent=False)
                continue

            is_active_group = any(route == active_route for route, _ in items)
            with ui.expansion(group_label, value=is_active_group).classes(
                "w-full leg-nav-group"
            ).props("dense"):
                for route, label in items:
                    _nav_link(route, label, active_route, indent=True)

    with ui.column().classes("w-full max-w-5xl mx-auto p-4") as content:
        yield content
