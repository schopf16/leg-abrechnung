"""The one sorting control used by every list in the app."""

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence, Union

from nicegui import ui

from app.sort_keys import (
    address_key,
    fold_for_sort,
    natural_key,
    number_key,
    person_name_key,
    text_key,
)

__all__ = [
    "SortControl",
    "SortOption",
    "address_key",
    "apply_sort",
    "fold_for_sort",
    "natural_key",
    "number_key",
    "person_name_key",
    "render_sort_select",
    "sort_description",
    "text_key",
]


@dataclass(frozen=True)
class SortOption:
    """One entry of a page's "Sortierung" select."""

    key: str
    label: str
    sort_key: Callable[[Any], Any] = field(compare=False)
    reverse: bool = False


class SortControl:
    """The "Sortierung" control: the select plus its direction toggle."""

    def __init__(self, select: ui.select, button: ui.button, on_change: Callable[[], None]) -> None:
        """Wire the toggle button to its own state and the page's refresh."""
        self._select = select
        self._button = button
        self._on_change = on_change
        self.descending = False
        self._watchers: list[Callable[[], None]] = []
        button.on_click(self._toggle)
        select.on_value_change(lambda _: self._notify())

    def set_value(self, key: str, *, descending: bool = False) -> None:
        """Put the control back the way a previous visit left it."""
        self._select.value = key
        self.descending = descending
        self._show_direction()

    def _show_direction(self) -> None:
        """Point the arrow the way the current direction says."""
        self._button.props(f"icon={'arrow_downward' if self.descending else 'arrow_upward'}")

    def on_any_change(self, watcher: Callable[[], None]) -> None:
        """Call `watcher` after the key or the direction changes."""
        self._watchers.append(watcher)

    def _notify(self) -> None:
        """Tell the watchers the selection changed."""
        for watcher in self._watchers:
            watcher()

    @property
    def value(self) -> Optional[str]:
        """The currently selected option key."""
        return self._select.value

    def _toggle(self) -> None:
        """Flip the direction, update the arrow, and refresh the page."""
        self.descending = not self.descending
        self._show_direction()
        self._notify()
        self._on_change()


def render_sort_select(
    options: Sequence[SortOption],
    on_change: Callable[[], None],
    *,
    value: Optional[str] = None,
) -> SortControl:
    """Render the standard "Sortierung" control for a list view."""
    select = ui.select(
        {option.key: option.label for option in options},
        value=value or options[0].key,
        label="Sortierung",
    ).classes("w-full max-w-xs")
    select.on_value_change(lambda _: on_change())
    button = ui.button(icon="arrow_upward").props("flat dense")
    button.tooltip("Auf-/absteigend sortieren")
    return SortControl(select, button, on_change)


def _resolve(
    options: Sequence[SortOption], selected: Union[str, None, SortControl]
) -> tuple[SortOption, bool]:
    """Resolve a selection into the option to use and the direction."""
    key = getattr(selected, "value", selected)
    descending = bool(getattr(selected, "descending", False))
    return next((o for o in options if o.key == key), options[0]), descending


def apply_sort(
    rows: Iterable[Any], options: Sequence[SortOption], selected: Union[str, None, SortControl]
) -> list:
    """Sort rows by the selected option, in the selected direction."""
    option, descending = _resolve(options, selected)
    # XOR: an option that is inherently descending and a reversed
    # direction cancel out, rather than the arrow doing nothing.
    return sorted(rows, key=option.sort_key, reverse=option.reverse != descending)


def sort_description(options: Sequence[SortOption], selected: Union[str, None, SortControl]) -> str:
    """Name the active order for the printout's "Sortierung:" line."""
    option, descending = _resolve(options, selected)
    return f"nach {option.label}" + (" (absteigend)" if descending else "")
