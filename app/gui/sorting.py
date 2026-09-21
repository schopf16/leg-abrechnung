"""The one sorting control used by every list in the app.

Every list view sorts the same way: a select labelled "Sortierung", always
the last control in the page's filter row, always offering a handful of
named orders with a sensible default. There is deliberately no second
mechanism -- the `ui.table` views used to let you click a column header
while the card views offered nothing at all, so the same question ("how do
I sort this?") had a different answer on nearly every page.

Cards are the reason a select rather than clickable headers won: several
lists render as cards on purpose (a long Bemerkung must wrap instead of
forcing horizontal scrolling, see `app.gui.pages.persons`), and cards have
no column headers to click. A select works for both shapes.

Sorting happens in Python, on the rows a page has already loaded, rather
than in each repo's `ORDER BY`. That keeps the option list next to the
page that shows it, and it applies `fold_for_sort`, so umlauts sort as
their base letter the way a German reader expects.

The key functions themselves live in `app.sort_keys`, which imports no
NiceGUI, and are re-exported here: the PDF layer has to group a person's
sites in the same order the Standorte page shows them, and it cannot
import this module. Pages keep importing them from here.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence, Union

from nicegui import ui

from app.sort_keys import (
    address_key,
    fold_for_sort,
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
    "number_key",
    "person_name_key",
    "render_sort_select",
    "sort_description",
    "text_key",
]


@dataclass(frozen=True)
class SortOption:
    """One entry of a page's "Sortierung" select.

    Attributes:
        key: Stable identifier, used as the select's value. Never shown.
        label: German label shown in the select.
        sort_key: Key function passed to `sorted`, receiving whatever the
            page keeps in its list (a row dict or a model instance).
        reverse: Whether to sort descending -- for "highest/newest first"
            orders. Note this also flips ties.
    """

    key: str
    label: str
    sort_key: Callable[[Any], Any] = field(compare=False)
    reverse: bool = False


class SortControl:
    """The "Sortierung" control: the select plus its direction toggle.

    Exposes `value` (the selected key) so a page can read it exactly as it
    would read the select itself, and `descending`, which the arrow button
    flips. Pass the whole control to `apply_sort`/`sort_description` and
    both parts are taken into account.
    """

    def __init__(self, select: ui.select, button: ui.button, on_change: Callable[[], None]) -> None:
        """Wire the toggle button to its own state and the page's refresh.

        Args:
            select: The rendered "Sortierung" select.
            button: The ascending/descending toggle button.
            on_change: The page's refresh, called after a direction flip.
        """
        self._select = select
        self._button = button
        self._on_change = on_change
        self.descending = False
        button.on_click(self._toggle)

    @property
    def value(self) -> Optional[str]:
        """The currently selected option key."""
        return self._select.value

    def _toggle(self) -> None:
        """Flip the direction, update the arrow, and refresh the page."""
        self.descending = not self.descending
        self._button.props(f"icon={'arrow_downward' if self.descending else 'arrow_upward'}")
        self._on_change()


def render_sort_select(
    options: Sequence[SortOption],
    on_change: Callable[[], None],
    *,
    value: Optional[str] = None,
) -> SortControl:
    """Render the standard "Sortierung" control for a list view.

    Always looks and sits the same: same label, same width, last control in
    the page's filter row, with the direction arrow immediately beside it.
    Call it from every list that offers more than one sensible order; a
    list with only one sensible order simply sorts that way and shows no
    control.

    The arrow exists because the `ui.table` pages this mechanism replaced
    could sort descending by clicking a column header twice. One toggle
    that applies to whichever order is selected keeps that possible
    without doubling the length of every select.

    Args:
        options: The orders this list offers; the first is the default.
        on_change: Called whenever the selection or direction changes --
            normally the page's `refresh`.
        value: Initially selected key, defaulting to the first option.

    Returns:
        The `SortControl`, whose `value`/`descending` the page reads.
    """
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
    """Resolve a selection into the option to use and the direction.

    Read by attribute rather than by `isinstance`, so a bare key still
    works (as the tests use) and neither function needs a rendered
    NiceGUI page to be exercised.

    Args:
        options: The page's options.
        selected: A `SortControl`, or a bare key.

    Returns:
        `(option, descending)`; an unknown or missing key falls back to
        the first option, so a page always has a defined order even
        before the control has been touched.
    """
    key = getattr(selected, "value", selected)
    descending = bool(getattr(selected, "descending", False))
    return next((o for o in options if o.key == key), options[0]), descending


def apply_sort(
    rows: Iterable[Any], options: Sequence[SortOption], selected: Union[str, None, SortControl]
) -> list:
    """Sort rows by the selected option, in the selected direction.

    Args:
        rows: The rows to sort; left untouched.
        options: The same options passed to `render_sort_select`.
        selected: The page's `SortControl`, or a bare option key.

    Returns:
        A new, sorted list. An option that already reads "meiste zuerst"
        (because its key negates a number) flips back to fewest-first when
        the direction is reversed, which is what the arrow is for.
    """
    option, descending = _resolve(options, selected)
    # XOR: an option that is inherently descending and a reversed
    # direction cancel out, rather than the arrow doing nothing.
    return sorted(rows, key=option.sort_key, reverse=option.reverse != descending)


def sort_description(options: Sequence[SortOption], selected: Union[str, None, SortControl]) -> str:
    """Name the active order for the printout's "Sortierung:" line.

    The printed list is read away from the screen, where neither the order
    nor its direction is self-evident.

    Args:
        options: The page's options.
        selected: The page's `SortControl`, or a bare option key.

    Returns:
        A German phrase such as `"nach Nachname"`, or
        `"nach Nachname (absteigend)"`.
    """
    option, descending = _resolve(options, selected)
    return f"nach {option.label}" + (" (absteigend)" if descending else "")
