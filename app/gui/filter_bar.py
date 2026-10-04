"""The one filter bar every browsable list is built with.

Third member of the family `app.gui.sorting` and `app.gui.problem_markers`
started, and built for the same complaint: *"bei jeder neuen idee packen wir
einfach nach etwas hinten an"*. Each list used to lay out its own controls in
one `ui.row`, so every new filter went on the end of whatever was there --
and three lists ended up with the problem filter before the sort control
while two had it after.

The bar owns the layout, the page only says what it needs. Two columns:
search and sort on the left, filters stacked on the right.

**Why two columns and not one row.** The problem filter appears and
disappears with the findings (`app.gui.problem_markers`), and in one row that
moved every control beside it -- the administrator toggled it and watched the
sort control jump. Stacked in their own column, a filter that comes or goes
moves nothing else on the screen.

**Conditional filters render last whatever order the page asks for them in**,
because the bar keeps a container at the bottom of that column for them. That
is structure rather than a convention to remember, which is the whole point
of having one component.

**A filter's text is clickable**, via Quasar's `label` *prop*. NiceGUI's
`ui.switch(text)` puts the text in the element's default slot instead, which
Quasar renders beside the switch but does not wire up -- so the administrator
hit a word that did nothing. There is no count on any of them: a number on
one filter and not the others reads as though the others had nothing to
count, and a number on all four makes the column unreadable.
"""

from typing import Callable, Optional, Sequence

from nicegui import ui

from app.gui.problem_markers import ProblemFilter
from app.gui.sorting import SortControl, SortOption, render_sort_select


class FilterBar:
    """Search, sort and filters for one list page.

    Attributes:
        left: The column holding search and sort, for the rare page that has
            something else to put there.
    """

    def __init__(self) -> None:
        """Lay the bar out, before any control is asked for.

        Returns:
            None.
        """
        with ui.row().classes("w-full items-start justify-between gap-6"):
            self.left = ui.column().classes("gap-2 grow")
            with ui.column().classes("gap-1 items-start shrink-0"):
                self._filters = ui.column().classes("gap-1 items-start")
                #: Separate container so a filter that only exists while
                #: there is something to filter cannot land above the
                #: permanent ones, whatever order the page creates them in.
                self._conditional = ui.column().classes("gap-1 items-start")

    def search(self, fields: str) -> ui.input:
        """Render the search field.

        The label is just "Suche". It used to be the whole list of fields
        searched, which Quasar shrinks to caption size above the input as
        soon as anything is typed -- unreadable exactly when it is being
        used. The list is the hint underneath instead, where it stays put.

        Args:
            fields: What the search covers, e.g. "Name, Firma, Adresse".
                Must not contain a double quote, since it is passed through
                Quasar's `hint` prop.

        Returns:
            The input, for the page to read and wire up.
        """
        assert '"' not in fields, fields
        with self.left:
            return (
                ui.input("Suche")
                .classes("w-full max-w-md")
                .props(f'debounce=300 clearable dense hint="{fields}"')
            )

    def sort(self, options: Sequence[SortOption], on_change: Callable[[], None]) -> SortControl:
        """Render the sort control below the search field.

        Args:
            options: The page's `SORT_OPTIONS` (default first).
            on_change: The page's refresh.

        Returns:
            The `SortControl`, to be passed whole to `apply_sort`.
        """
        with self.left:
            return render_sort_select(options, on_change)

    def filter(self, label: str, *, value: bool = False) -> ui.switch:
        """Render one permanent filter, with a clickable label.

        Args:
            label: The German label. Must not contain a double quote, since
                it is passed through Quasar's `label` prop.
            value: Whether it starts switched on.

        Returns:
            The switch, for the page to read and wire up.
        """
        assert '"' not in label, label
        with self._filters:
            return ui.switch(value=value).props(f'label="{label}" dense')

    def choice(
        self,
        options: dict | list,
        label: str,
        *,
        value: Optional[object] = None,
    ) -> ui.select:
        """Render a filter that picks one of several values, not on/off.

        Args:
            options: Quasar select options.
            label: The German label.
            value: Initially selected value.

        Returns:
            The select, for the page to read and wire up.
        """
        with self._filters:
            return ui.select(options, value=value, label=label).classes("w-60").props("dense")

    def problem_filter(self, on_change: Callable[[], None]) -> ProblemFilter:
        """Render "Nur fehlerhafte Einträge", always at the bottom.

        Args:
            on_change: The page's refresh.

        Returns:
            The filter, for the page to `update()` with each refresh.
        """
        with self._conditional:
            return ProblemFilter(on_change)
