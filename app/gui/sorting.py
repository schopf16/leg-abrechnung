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
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

from nicegui import ui


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


def fold_for_sort(text: Optional[str]) -> str:
    """Fold text so it sorts the way a German reader expects.

    Umlauts sort as their base letter (DIN 5007 Variant 1: "Bühler" before
    "Burri", "Köhle" before "Kuhny"), "ß" as "ss", and accents on
    French-Swiss names the same way.

    Plain code-point ordering -- what SQLite's BINARY/NOCASE collation
    does, so what every repo `ORDER BY` in this app does -- gets this
    wrong in two different ways: a non-leading umlaut lands after its own
    initial group ("Bühler" after "Burri", "Zürcher" after "Zwahlen"),
    and a *leading* umlaut goes past every "Z..." name ("Ärni" last of
    all). Verified against SQLite, not assumed.

    Args:
        text: Raw text, or `None`.

    Returns:
        A lowercased, accent-free version, for comparison only -- never
        shown to anyone. `None` becomes `""`.
    """
    decomposed = unicodedata.normalize("NFD", (text or "").strip().replace("ß", "ss"))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def text_key(*values: Optional[str]) -> tuple[str, ...]:
    """Build a folded sort key from several text parts, in priority order.

    Args:
        *values: Text parts, e.g. surname then first name.

    Returns:
        A tuple of folded strings, comparable with `<`.
    """
    return tuple(fold_for_sort(value) for value in values)


def person_name_key(person: Any) -> tuple[str, ...]:
    """Build the surname-first sort key for a Person.

    Follows `person_repo.list_all`'s rule -- surname, falling back to the
    company name for a company without a contact person, then first name
    -- so the same people come out in the same order on every page that
    lists them, not just on the Personen page.

    Args:
        person: A `app.models.person.Person`, or `None` when a row's
            person is gone (a page must not crash over that).

    Returns:
        A folded `(primary, first_name)` key; a missing person sorts first.
    """
    if person is None:
        return ("", "")
    return text_key(person.last_name.strip() or person.company, person.first_name)


#: Splits the house number off an address: "Fischrain 68a" becomes
#: ("Fischrain", "68", "a"). Anything without a number stays whole.
#:
#: Anchored on the *first* number of the tail, not the last, and the
#: remainder may itself contain digits: `house_number` is free text (see
#: `app.models.site.Site`), so a range or a compound ("12-14", "12/14")
#: does occur. Matching the last number would key those as street
#: "Fischrain 12-" and drop them out of the Fischrain group entirely --
#: exactly the misordering this function exists to prevent.
_HOUSE_NUMBER_TAIL = re.compile(r"^(.*?)\s*(\d+)\s*(.*)$")


def address_key(street: Optional[str], house_number: Optional[str] = "", *rest: Optional[str]) -> tuple:
    """Build a sort key for a postal address, ordering the house number
    numerically.

    "Fischrain 9" belongs before "Fischrain 68"; compared as plain text,
    "68" sorts before "9". Accepts either shape a page happens to have:
    street and house number as separate fields, or one combined
    `"Fischrain 68"` string with the number already appended.

    Args:
        street: Street name, with or without the house number appended.
        house_number: House number, if the page keeps it separately.
        *rest: Further parts to break ties, in priority order -- normally
            the municipality.

    Returns:
        `(street, number, suffix, *rest)`, where an address without any
        house number sorts before the numbered ones on the same street.
    """
    combined = " ".join(part for part in (street or "", house_number or "") if part).strip()
    match = _HOUSE_NUMBER_TAIL.match(combined)
    if match:
        name, number, suffix = match.group(1), int(match.group(2)), match.group(3)
    else:
        name, number, suffix = combined, -1, ""
    return (fold_for_sort(name), number, fold_for_sort(suffix)) + text_key(*rest)


def number_key(value: Optional[float], *, missing_last: bool = True) -> tuple[int, float]:
    """Build a sort key for an optional number, keeping blanks together.

    Args:
        value: The number, or `None` when the field is not filled in.
        missing_last: Whether missing values sort after present ones.
            Note that with `SortOption.reverse` this flips along with
            everything else.

    Returns:
        `(presence, value)`, where `presence` groups missing values.
    """
    if value is None:
        return (1 if missing_last else -1, 0.0)
    return (0, float(value))


def render_sort_select(
    options: Sequence[SortOption],
    on_change: Callable[[], None],
    *,
    value: Optional[str] = None,
) -> ui.select:
    """Render the standard "Sortierung" select for a list view.

    Always looks and sits the same: same label, same width, last control in
    the page's filter row. Call it from every list that offers more than
    one sensible order; a list with only one sensible order simply sorts
    that way and shows no control.

    Args:
        options: The orders this list offers; the first is the default.
        on_change: Called whenever the selection changes -- normally the
            page's `refresh`.
        value: Initially selected key, defaulting to the first option.

    Returns:
        The created `ui.select`, so the page can read `.value`.
    """
    select = ui.select(
        {option.key: option.label for option in options},
        value=value or options[0].key,
        label="Sortierung",
    ).classes("w-full max-w-xs")
    select.on_value_change(lambda _: on_change())
    return select


def apply_sort(rows: Iterable[Any], options: Sequence[SortOption], selected_key: Optional[str]) -> list:
    """Sort rows by the selected option.

    Args:
        rows: The rows to sort; left untouched.
        options: The same options passed to `render_sort_select`.
        selected_key: Currently selected key; an unknown or missing key
            falls back to the first option, so a page always has a defined
            order even before the select has been touched.

    Returns:
        A new, sorted list.
    """
    option = next((o for o in options if o.key == selected_key), options[0])
    return sorted(rows, key=option.sort_key, reverse=option.reverse)


def sort_description(options: Sequence[SortOption], selected_key: Optional[str]) -> str:
    """Name the active order for a printout's filter line.

    The printed list is read away from the screen, where the order is not
    self-evident.

    Args:
        options: The page's options.
        selected_key: Currently selected key.

    Returns:
        A German phrase such as `"nach Nachname"`, to follow the
        printout's own "Sortierung:" label.
    """
    option = next((o for o in options if o.key == selected_key), options[0])
    return f"nach {option.label}"
