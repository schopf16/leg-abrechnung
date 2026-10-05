"""Shared page shell: header, side navigation and page-content container."""

from contextlib import contextmanager
from typing import Iterator, Optional

from nicegui import app, ui

from app.gui.global_search import render_global_search
from app.gui.keyboard import install as install_keyboard
from app.gui.print_list import PRINT_STYLE
from app.version import APP_VERSION

#: Side navigation. Read top to bottom it is the administrator's year:
#: what needs doing -> who and what is in the LEG -> what is moving ->
#: the quarter's billing -> looking back -> sending -> the tools. Each
#: entry is ``(group_label, [(route, label), ...])``; ``group_label`` of
#: ``None`` renders its items without a collapsible section (used for the
#: single top-level "Übersicht" entry).
#:
#: "Stammdaten" and "Vorgänge" are deliberately two chapters rather than
#: one "Verwaltung": the first is what the LEG *is* and is corrected, the
#: second is work in progress and is worked off. That is also the boundary
#: the problem markers follow (see `app.gui.problem_markers`) -- a
#: half-filled onboarding is not a defect, so those two lists carry none.
NAV_GROUPS: list[tuple[Optional[str], list[tuple[str, str]]]] = [
    (None, [("/", "Übersicht")]),
    (
        "Stammdaten",
        [
            ("/substation-areas", "Trafokreise"),
            ("/sites", "Standorte"),
            ("/legs", "LEGs"),
            ("/metering-points", "Messpunkte"),
            ("/persons", "Personen"),
            ("/assignments", "Zuordnungen"),
        ],
    ),
    (
        "Vorgänge",
        [
            ("/web-registrations", "Web-Registrierungen"),
            ("/onboardings", "Aufnahmen"),
            ("/offboardings", "Austritte"),
        ],
    ),
    (
        # The order of the quarter: import, check what the import contains,
        # bill it, book the payments, chase what is missing. "Auswertungen"
        # used to sit last although it is the control sheet read *before*
        # the run -- `quarter_energy_totals` is what says whether a quarter
        # is worth billing at all.
        "Abrechnung",
        [
            ("/import", "Import"),
            ("/reports", "Auswertungen"),
            ("/billing", "Rechnungslauf"),
            ("/receivables", "Debitoren"),
            ("/dunning", "Mahnwesen"),
        ],
    ),
    (
        "Statistik",
        [
            ("/statistics/energy", "Energie"),
            ("/statistics/growth", "Wachstum"),
            ("/statistics/receivables", "Debitorenverlauf"),
            ("/statistics/distribution", "Verteilung"),
            ("/statistics/balance", "Ausgewogenheit"),
            ("/statistics/potential", "Potenzial"),
        ],
    ),
    (
        "Kommunikation",
        [
            ("/email-dispatch", "E-Mail versenden"),
            ("/message-templates", "Textbausteine"),
            ("/signatures", "Signaturen"),
        ],
    ),
    (
        "Einstellungen",
        [
            ("/settings", "Allgemein"),
            ("/address-register", "Adressregister"),
            ("/backup", "Backup"),
        ],
    ),
]


def _nav_link(route: str, label: str, active_route: str, *, indent: bool) -> None:
    """Render one navigation link, highlighted if it matches the current page."""
    classes = "w-full leg-nav-item" + (" leg-nav-active" if route == active_route else "")
    padding = "6px 12px 6px 28px" if indent else "6px 12px"
    ui.link(label, route).classes(classes).style(f"display:block; padding:{padding};")


def _confirm_quit() -> None:
    """Ask for confirmation, then cleanly shut down the application."""
    with ui.dialog() as dialog, ui.card():
        ui.label("LEG-Abrechnung wirklich beenden?")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            ui.button("Beenden", on_click=app.shutdown, color="negative")
    dialog.open()


@contextmanager
def page_frame(active_route: str, title: str) -> Iterator[None]:
    """Render the common header and drawer, yielding a container for content."""
    ui.add_head_html(
        "<style>"
        # An entry is navigation, not a link inside a text: blue and
        # underlined reads as "this leaves the page". They are rows.
        ".leg-nav-item { color: rgba(0,0,0,0.75); text-decoration: none; }"
        ".leg-nav-item:hover { background: rgba(0,0,0,0.04); }"
        # The open entry carries one grey bar across the full drawer width,
        # the same width for every label -- which is what makes the drawer
        # scannable instead of read. The chapter headings stay black like
        # all the others; colouring the open one made it look like a link.
        ".leg-nav-active { background: rgba(0,0,0,0.10); font-weight: 700;"
        " color: rgba(0,0,0,0.87); }"
        ".leg-nav-group .q-item { padding: 6px 12px; min-height: 0; }"
        ".leg-nav-group .q-item__label { font-size: 13px; font-weight: 600; }"
        # A list is there to be read *and* used elsewhere: a Kunden-Nr. gets
        # pasted into a bank form, an address into a letter. Quasar renders
        # a table inside `.non-selectable`, whose rule carries `!important`,
        # so undoing it needs the same weight. The buttons in the actions
        # column keep their own `non-selectable`, as buttons should.
        ".leg-selectable, .leg-selectable td, .leg-selectable th "
        "{ -webkit-user-select: text !important; user-select: text !important; }"
        ".leg-selectable .q-btn { -webkit-user-select: none !important;"
        " user-select: none !important; }"
        # `wrap-cells` wraps between words; "CH1018000000000000000000001"
        # has none, so without this it still forces the table wider than
        # the window.
        ".leg-selectable td { word-break: break-word; }"
        "</style>"
    )
    ui.add_head_html(PRINT_STYLE)
    ui.page_title(f"LEG-Abrechnung – {title}")
    # One keyboard for the window; who owns the keys is decided by the
    # stack in `app.gui.keyboard`, not by which element has the focus.
    install_keyboard()

    with ui.header().classes("items-center justify-between bg-primary text-white"):
        ui.label("LEG-Abrechnung").classes("text-lg font-bold")
        ui.label(title).classes("text-md")
        # One box that reaches every Stammdaten record, on every page: a
        # record used to be findable only from the list it lives on, which
        # asked the reader to know this app's filing before looking
        # anything up. See `app.gui.global_search`.
        render_global_search()
        _render_address_register_progress()

    with ui.left_drawer(fixed=True).classes("bg-grey-1 q-pa-none").props("width=240"):
        for group_label, items in NAV_GROUPS:
            if group_label is None:
                for route, label in items:
                    _nav_link(route, label, active_route, indent=False)
                continue

            is_active_group = any(route == active_route for route, _ in items)
            with (
                ui.expansion(group_label, value=is_active_group)
                .classes("w-full leg-nav-group")
                # An accordion: opening one chapter closes the rest, so the
                # open chapter *is* the answer to "which part am I in" and
                # needs no second mark of its own. Browsing another chapter
                # hides the grey bar for as long as it stays open, which
                # ends at the next click -- navigating re-renders the drawer
                # with the chapter of the new page open.
                .props("dense group=leg-nav")
            ):
                for route, label in items:
                    _nav_link(route, label, active_route, indent=True)

        ui.separator()
        ui.button("Beenden", icon="power_settings_new", on_click=_confirm_quit).props(
            "flat color=negative align=left"
        ).classes("w-full")

        # Always visible at the bottom, on every page -- lets the
        # administrator read out a phone-friendly version identifier
        # (commit date + short hash, see app.version) to check whether
        # someone else's installation is on the latest push.
        ui.label(f"Version {APP_VERSION}").classes("text-caption text-grey-6 q-pa-sm").style(
            "position: absolute; bottom: 0; left: 0;"
        )

    # `max-w-5xl` was 1024 px, set when every list was cards that wrapped
    # into whatever space they had. A table cannot wrap: too narrow and it
    # scrolls sideways, and a sideways scrollbar sits at the *bottom* of a
    # long list -- so reading the right-hand columns means scrolling down,
    # across, and back up. Still bounded rather than full width, because a
    # settings form or a detail page with a line of text across 1920 px is
    # unreadable for the opposite reason.
    with ui.column().classes("w-full max-w-screen-2xl mx-auto p-4") as content:
        yield content


def _render_address_register_progress() -> None:
    """Show the running address-register update in the header, everywhere."""
    from app.gui.address_register_task import STATE

    label = ui.label().classes("text-caption")

    def refresh() -> None:
        """Copy the current phase and percentage into the header label."""
        label.text = STATE.label
        label.visible = bool(STATE.label)

    refresh()
    ui.timer(1.0, refresh)
