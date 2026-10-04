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

from app.gui.list_state import recall, remember
from app.gui.problem_markers import ProblemFilter
from app.gui.sorting import SortControl, SortOption, render_sort_select


class FilterBar:
    """Search, sort and filters for one list page.

    Attributes:
        left: The column holding search and sort, for the rare page that has
            something else to put there.
    """

    def __init__(self, route: str = "") -> None:
        """Lay the bar out, before any control is asked for.

        Args:
            route: The list's route. Given one, every control the bar hands
                out comes back set the way it was left -- see
                `app.gui.list_state` for why that matters and what it costs.
                Empty for a bar whose state should not outlive the page.

        Returns:
            None.
        """
        self._route = route
        #: `(element, default)` for everything the bar handed out, so it can
        #: say whether anything is filtered and put it all back.
        self._defaults: list[tuple] = []
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
            field = (
                ui.input("Suche", value=self._recall("search", ""))
                .classes("w-full max-w-md")
                .props(f'debounce=300 clearable dense hint="{fields}"')
            )
        self._keep(field, "search")
        self._defaults.append((field, ""))
        return field

    def sort(self, options: Sequence[SortOption], on_change: Callable[[], None]) -> SortControl:
        """Render the sort control below the search field.

        Args:
            options: The page's `SORT_OPTIONS` (default first).
            on_change: The page's refresh.

        Returns:
            The `SortControl`, to be passed whole to `apply_sort`.
        """
        with self.left:
            control = render_sort_select(options, on_change)
        remembered = self._recall("sort")
        if remembered is not None:
            key, descending = remembered
            if any(option.key == key for option in options):
                control.set_value(key, descending=descending)
        control.on_any_change(lambda: self._store("sort", (control.value, control.descending)))
        return control

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
            switch = ui.switch(value=bool(self._recall(f"filter:{label}", value))).props(
                f'label="{label}" dense'
            )
        self._keep(switch, f"filter:{label}")
        self._defaults.append((switch, value))
        return switch

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
            select = (
                ui.select(options, value=self._recall(f"choice:{label}", value), label=label)
                .classes("w-60")
                .props("dense")
            )
        self._keep(select, f"choice:{label}")
        self._defaults.append((select, value))
        return select

    def problem_filter(self, on_change: Callable[[], None]) -> ProblemFilter:
        """Render "Nur fehlerhafte Einträge", always at the bottom.

        Args:
            on_change: The page's refresh.

        Returns:
            The filter, for the page to `update()` with each refresh.
        """
        with self._conditional:
            problem_filter = ProblemFilter(on_change)
        # Restored like the others, but it can only be *on* while something
        # is marked: `ProblemFilter.update` switches it off when the last
        # finding goes, and a correction is exactly what the administrator
        # was away doing.
        if self._recall("filter:problems", False):
            problem_filter.switch.value = True
        self._keep(problem_filter.switch, "filter:problems")
        self._defaults.append((problem_filter.switch, False))
        return problem_filter

    # -- resetting ----------------------------------------------------------

    def is_filtering(self) -> bool:
        """Whether any control is set to something other than its default.

        What an empty list uses to decide whether to offer a way out: with
        nothing filtered, "Filter zurücksetzen" would be a button that does
        nothing, and the list is simply empty.

        Returns:
            `True` if at least one control is away from its default.
        """
        # `clearable` sets a cleared input to `None`, not to `""`, so a
        # search box the administrator emptied with the X looked filtered --
        # and the empty list then offered "Filter zurücksetzen" for a button
        # that would change nothing.
        return any(
            (element.value or "") != (default or "") if isinstance(default, str) else element.value != default
            for element, default in self._defaults
        )

    def reset(self, then: Optional[Callable[[], None]] = None) -> None:
        """Put every control back to its default.

        Sets the values silently and then calls the page once, rather than
        letting five controls fire five refreshes.

        Args:
            then: The page's refresh.

        Returns:
            None.
        """
        for element, default in self._defaults:
            element.value = default
        if then is not None:
            then()

    # -- remembering --------------------------------------------------------

    def _recall(self, name: str, default=None):
        """What this control was set to last time, if the bar has a route.

        Args:
            name: The control's name within the list.
            default: Used for a list that has not been visited.

        Returns:
            The remembered value or `default`.
        """
        if not self._route:
            return default
        return recall(self._route, name, default)

    def _store(self, name: str, value) -> None:
        """Keep one value, if the bar has a route.

        Args:
            name: The control's name within the list.
            value: What to keep.

        Returns:
            None.
        """
        if self._route:
            remember(self._route, name, value)

    def _keep(self, element, name: str) -> None:
        """Keep this element's value whenever it changes.

        Args:
            element: Any NiceGUI value element.
            name: The control's name within the list.

        Returns:
            None.
        """
        if not self._route:
            return
        # Reads the element rather than the event: the element is the
        # source of truth either way, and a handler that only works when
        # NiceGUI supplies the arguments cannot be driven from a test.
        element.on_value_change(lambda _=None: self._store(name, element.value))
