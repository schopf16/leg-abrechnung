"""The one filter bar every browsable list is built with."""

from typing import Callable, Optional, Sequence

from nicegui import ui

from app.gui.list_state import recall, remember
from app.gui.problem_markers import ProblemFilter
from app.gui.sorting import SortControl, SortOption, render_sort_select


class FilterBar:
    """Search, sort and filters for one list page."""

    def __init__(self, route: str = "") -> None:
        """Lay the bar out, before any control is asked for."""
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
        """Render the search field."""
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
        """Render the sort control below the search field."""
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
        """Render one permanent filter, with a clickable label."""
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
        """Render a filter that picks one of several values, not on/off."""
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
        """Render "Nur fehlerhafte Einträge", always at the bottom."""
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
        """Whether any control is set to something other than its default."""
        # `clearable` sets a cleared input to `None`, not to `""`, so a
        # search box the administrator emptied with the X looked filtered --
        # and the empty list then offered "Filter zurücksetzen" for a button
        # that would change nothing.
        return any(
            (element.value or "") != (default or "") if isinstance(default, str) else element.value != default
            for element, default in self._defaults
        )

    def reset(self, then: Optional[Callable[[], None]] = None) -> None:
        """Put every control back to its default."""
        for element, default in self._defaults:
            element.value = default
        if then is not None:
            then()

    # -- remembering --------------------------------------------------------

    def _recall(self, name: str, default=None):
        """What this control was set to last time, if the bar has a route."""
        if not self._route:
            return default
        return recall(self._route, name, default)

    def _store(self, name: str, value) -> None:
        """Keep one value, if the bar has a route."""
        if self._route:
            remember(self._route, name, value)

    def _keep(self, element, name: str) -> None:
        """Keep this element's value whenever it changes."""
        if not self._route:
            return
        # Reads the element rather than the event: the element is the
        # source of truth either way, and a handler that only works when
        # NiceGUI supplies the arguments cannot be driven from a test.
        element.on_value_change(lambda _=None: self._store(name, element.value))
