"""The sort keys every list in this app orders by, free of any layer.

These live here rather than in `app.gui.sorting` because that module
imports NiceGUI, and the PDF layer must not: a billing document groups a
person's sites and has to put them in the same order the Standorte page
does. CLAUDE.md's rule -- "a person or an address must come out in the
same order on every page that lists it" -- only holds if there is one set
of key functions, so there is exactly one, and `app.gui.sorting`
re-exports it for the pages.

Same reasoning as `app.format_size`, which is layer-neutral because
`app.emailing` must not import from `app.gui`.
"""

import re
import unicodedata
from typing import Any, Optional


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
