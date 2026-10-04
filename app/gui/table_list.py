"""Paging for the lists that are tables, with one setting everywhere.

Why the Personen list became a table at all: it drew 92 cards of 23
interface elements each, 2'108 in total, and the administrator reported the
page taking close to a second to open. A Quasar table is **one** element
with its rows as data, which is why the Standorte page was always fast --
and it is also why the number of *columns* costs nothing, while the number
of cards cost everything.

Paging is Quasar's own, deliberately, and that is a different decision from
the one in `app.gui.sorting`. Sorting is forbidden to use Quasar's
`sortable: True` headers because half the lists are cards with no header to
click, so clickable headers could never work everywhere. Paging has no such
split: only a table can page, every table can, and the footer Quasar draws
already carries the arrows, the count and the rows-per-page select. Writing
a second one in Python would be two mechanisms for one job, which is what
this project keeps removing.

**The search and the filters run over everything, never over the page.** The
page builds its visible rows exactly as before -- filter, then sort, over
all records -- and hands the whole result to the table, which shows a window
onto it. So a search finds a person on page four and the printout holds
every filtered row, not the fifty on screen. `tests/test_persons_table.py`
pins both, because paging the wrong collection is the obvious way to build
this and looks right until the list is longer than one page.
"""

from nicegui import ui

#: Rows per page. 50 is the administrator's choice: the live deployment has
#: 92 persons and 92 sites, so 50 is "most of it, twice" rather than an
#: arbitrary round number.
DEFAULT_PAGE_SIZE = 50

#: What the select offers. `0` is Quasar's "all", kept because a list short
#: enough to read in one go should not have to be paged through -- and
#: because printing is per filter, not per page, somebody checking the
#: printout against the screen wants the whole thing.
PAGE_SIZE_OPTIONS = [30, 50, 100, 0]


def paged_table(**kwargs) -> ui.table:
    """Build a list table that pages, with German labels.

    Args:
        **kwargs: Passed to `ui.table` (`columns`, `rows`, `row_key`, ...).

    Returns:
        The table.
    """
    table = ui.table(pagination={"rowsPerPage": DEFAULT_PAGE_SIZE}, **kwargs)
    # Quasar's own footer: the arrows, "1-50 von 92", and the select. Its
    # labels are English by default, and this app is German throughout.
    table.props(f'rows-per-page-options="{PAGE_SIZE_OPTIONS}"')
    table.props('rows-per-page-label="Zeilen pro Seite"')
    table.props('no-data-label="Keine Einträge"')
    return table
