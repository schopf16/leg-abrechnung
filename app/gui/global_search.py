"""The search box in the header, on every page.

A record could only be found from the list it lives on: looking up a
Messpunktbezeichnung meant going to Messpunkte first, and a street meant
guessing whether it was filed under Standorte or under somebody's billing
address. That is knowledge about this app's filing, demanded of a reader who
only wants to find a meter.

The matching is `app.domain.global_search`; this is the box, the floating
list and the keys. Three decisions are shared with the address suggestions
(`app.gui.address_input`), for the same reasons and deliberately not
reinvented:

- The list **floats** over the page rather than sitting in the layout, so it
  cannot resize anything while the administrator is reading what they type.
- It takes the keys through `app.gui.keyboard` while it is open: arrows walk
  it, Enter opens the marked hit, Escape puts it away. Bound through the
  stack rather than on the field, because the menu takes no focus.
- **Enter opens nothing that was not stepped onto.** The first hit is marked
  as soon as there are results, so Enter after typing is one keystroke to
  the obvious answer -- but that mark is visible, which is the whole
  difference from guessing.

A group shows at most `PER_KIND_LIMIT` hits and says how many more there
are. A dropdown with ninety entries is a list, and a worse one than the real
page, so past that the group's own list is the answer.
"""

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.global_search import MIN_QUERY_LENGTH, SearchGroup, search
from app.gui.keyboard import KeyboardLayer, push, remove


class GlobalSearch:
    """The header's search box and its floating result list.

    Attributes:
        field: The input, so a test can type into it.
        menu: The floating list.
        groups: The current result groups.
        marked: Index of the marked hit across all groups, -1 for none.
    """

    def __init__(self) -> None:
        """Render the box, closed and empty.

        Returns:
            None.
        """
        self.groups: list[SearchGroup] = []
        self.marked = -1
        self._layer = KeyboardLayer(
            on_escape=self.hide,
            on_enter=self.open_marked,
            on_move=self.move,
        )

        self.field = (
            ui.input(placeholder="Suchen …")
            .props("dense dark standout clearable debounce=300")
            .classes("w-64")
        )
        with self.field:
            self.menu = ui.menu().props("no-focus no-refocus auto-close=false")
        self.field.on_value_change(lambda _=None: self.update())

    # -- searching ----------------------------------------------------------

    def update(self) -> None:
        """Run the search for whatever stands in the box.

        Returns:
            None.
        """
        query = (self.field.value or "").strip()
        if len(query) < MIN_QUERY_LENGTH:
            self.groups = []
        else:
            with connection_scope() as connection:
                self.groups = search(connection, query)
        # The first hit is marked straight away, so Enter after typing is one
        # keystroke to the obvious answer -- and the mark says which.
        self.marked = 0 if self.groups else -1
        self._render()

    def hide(self) -> None:
        """Put the list away, leaving the typed text alone.

        Returns:
            None.
        """
        self.groups = []
        self.marked = -1
        self._render()

    # -- the keys -----------------------------------------------------------

    def _flat(self) -> list:
        """Every shown hit, in the order they are drawn.

        Returns:
            The hits across all groups.
        """
        return [hit for group in self.groups for hit in group.hits]

    def move(self, step: int) -> None:
        """Walk the list by one hit, wrapping.

        Args:
            step: `-1` or `+1`.

        Returns:
            None.
        """
        hits = self._flat()
        if not hits:
            return
        self.marked = (self.marked + step) % len(hits)
        self._render()

    def open_marked(self) -> None:
        """Go where the marked hit leads.

        Returns:
            None.
        """
        hits = self._flat()
        if 0 <= self.marked < len(hits):
            self.open(hits[self.marked].route)

    def open(self, route: str) -> None:
        """Leave for one hit's page.

        The box is emptied first: coming back to a page with yesterday's
        query still in the header and no list under it reads as broken.

        Args:
            route: Where to go.

        Returns:
            None.
        """
        self.groups = []
        self.marked = -1
        self.field.value = ""
        self.menu.close()
        remove(self._layer)
        ui.navigate.to(route)

    # -- drawing ------------------------------------------------------------

    def _render(self) -> None:
        """Redraw the floating list.

        Returns:
            None.
        """
        if not self.groups:
            self.menu.close()
            remove(self._layer)
            return

        push(self._layer)
        self.menu.clear()
        index = 0
        with self.menu, ui.column().classes("gap-0 p-0 min-w-[320px]"):
            for group in self.groups:
                heading = group.label
                if group.total > len(group.hits):
                    heading = f"{group.label} ({len(group.hits)} von {group.total})"
                ui.label(heading).classes("text-caption text-grey-6 q-px-md q-pt-sm uppercase")
                for hit in group.hits:
                    self._render_hit(hit, marked=index == self.marked)
                    index += 1
        self.menu.open()

    def _render_hit(self, hit, *, marked: bool) -> None:
        """Draw one hit as a row.

        Args:
            hit: The `SearchHit`.
            marked: Whether the keys are currently on it.

        Returns:
            None.
        """
        row = (
            ui.button(on_click=lambda _=None, route=hit.route: self.open(route))
            .props("flat dense align=left no-caps")
            .classes("w-full")
        )
        if marked:
            # The same grey bar the drawer marks the open chapter with,
            # rather than a third way of saying "this one".
            row.style("background: rgba(0,0,0,0.10);")
        with row, ui.column().classes("gap-0 items-start"):
            ui.label(hit.title).classes("text-body2")
            if hit.detail:
                ui.label(hit.detail).classes("text-caption text-grey-6")


def render_global_search() -> GlobalSearch:
    """Put the search box in the page's header.

    Returns:
        The control, so a test can drive it.
    """
    return GlobalSearch()
