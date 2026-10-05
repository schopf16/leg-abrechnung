"""Paging for the lists that are tables, with one setting everywhere."""

from nicegui import ui

from app.gui.list_state import recall, remember

#: Rows per page. 50 is the administrator's choice: the live deployment has
#: 92 persons and 92 sites, so 50 is "most of it, twice" rather than an
#: arbitrary round number.
DEFAULT_PAGE_SIZE = 50

#: What the select offers. `0` is Quasar's "all", kept because a list short
#: enough to read in one go should not have to be paged through -- and
#: because printing is per filter, not per page, somebody checking the
#: printout against the screen wants the whole thing.
PAGE_SIZE_OPTIONS = [30, 50, 100, 0]


def paged_table(*, route: str = "", **kwargs) -> ui.table:
    """Build a list table that pages, with German labels."""
    pagination = {"rowsPerPage": DEFAULT_PAGE_SIZE}
    if route:
        pagination = dict(recall(route, "pagination", pagination))
    table = ui.table(pagination=pagination, **kwargs)
    if route:
        # Quasar reports the whole pagination object -- page, rowsPerPage,
        # sortBy -- so keeping it whole keeps the page *and* the page size,
        # which is the other half of what was being re-chosen every visit.
        table.on_pagination_change(lambda event: remember(route, "pagination", event.value))
    # Assigned, not passed through `props()`: that parses a string, so
    # Quasar received the literal text "[30, 50, 100, 0]" and the select had
    # nothing to offer -- the administrator found the 50 unchangeable. An
    # array prop has to reach the browser as an array.
    table._props["rows-per-page-options"] = list(PAGE_SIZE_OPTIONS)
    # Quasar's own footer otherwise: the arrows, "1-50 von 92" and the
    # select. Its labels are English by default and this app is German.
    table.props('rows-per-page-label="Zeilen pro Seite"')
    table.props('no-data-label="Keine Einträge"')
    # Quasar marks a table non-selectable, so a cell could be read but not
    # copied -- and a Kunden-Nr. is there to be pasted somewhere else. The
    # buttons in the actions column stay unselectable, as buttons are.
    table.classes("leg-selectable")
    # Quasar keeps a cell on one line by default, so one long value decides
    # the whole table's width -- a Messpunktbezeichnung is 27 characters and
    # unbreakable, and seven columns of that scrolled sideways. `wrap-cells`
    # lets a row grow taller instead, which is the trade the administrator
    # asked for: "wenn die breite nicht platz hat kürze den inhalt".
    table.props("wrap-cells")
    return table
