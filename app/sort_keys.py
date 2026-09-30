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


#: Splits a string into its digit runs, keeping them as separate parts.
#: `re.split` with a capturing group always yields text, number, text,
#: number, ..., text -- an odd-length list that starts and ends with a
#: (possibly empty) text part. That guarantee is what makes `natural_key`
#: type-safe: even positions are always a `str`, odd ones always a
#: `numeric_part` tuple, so no comparison ever puts one against the other.
_DIGIT_RUN = re.compile(r"(\d+)")


def numeric_part(digits: str) -> tuple[int, str]:
    """Turn a run of digits into a part that sorts numerically.

    Deliberately **not** `int(digits)`. Since Python 3.11 converting a
    string of more than 4300 digits raises `ValueError`
    (`sys.get_int_max_str_digits`), and the fields these keys are built from
    are not all typed by the administrator: `app.importers.cloudflare_client`
    takes names and addresses straight from the leg-ittigen.ch web form,
    which imposes no length limit, and those go into `person_name_key` and
    `address_key`. One submission with a long enough run of digits would
    have made the Webanmeldungen inbox raise instead of render.

    Comparing `(length, digits)` needs no conversion and is exactly as
    correct: with leading zeros stripped, a longer run of digits *is* the
    larger number, and two runs of the same length compare the same way
    lexicographically as numerically.

    Args:
        digits: One or more digits, nothing else.

    Returns:
        `(length, digits)` with leading zeros removed, so "007" and "7"
        produce the same part and tie.
    """
    stripped = digits.lstrip("0") or "0"
    return (len(stripped), stripped)


def natural_key(text: Optional[str]) -> tuple:
    """Build a sort key that reads numbers inside text as numbers.

    "TRA9365" belongs before "TRA19400"; compared character by character,
    "1" beats "9" and the longer number sorts first. The Trafokreis names
    BKW hands out run from three to five digits, so a plain text order puts
    every five-digit circuit ahead of every four-digit one -- which is what
    the administrator saw. Same for a `MeteringPoint.label` like
    "Whg. 3. OG" against "Whg. 10. OG".

    Leading zeros are not preserved: "TRA007" and "TRA7" produce the same
    key and sort as a tie, which `sorted` resolves by leaving them in the
    order they came in. Two circuits differing only in padding do not occur
    -- `substation_area.name` is unique.

    Args:
        text: Raw text, or `None`.

    Returns:
        Alternating folded text and `numeric_part` values, comparable
        with `<`.
    """
    parts = _DIGIT_RUN.split(fold_for_sort(text))
    return tuple(numeric_part(part) if index % 2 else part for index, part in enumerate(parts))


def text_key(*values: Optional[str]) -> tuple[tuple, ...]:
    """Build a folded, number-aware sort key from several text parts.

    This is the key every list in the app orders text by, so the numbers
    inside a name are read as numbers *everywhere* -- not only on the page
    whose own list prompted it. The Trafokreis is sorted on its own page,
    on the Standorte page and twice on the LEG detail page; fixing one of
    them would have left the app with two different orders for one name,
    which is the exact failure this module exists to prevent.

    Args:
        *values: Text parts, e.g. surname then first name.

    Returns:
        One `natural_key` per part, nested so that each part's own
        text/number boundaries cannot bleed into the next part's.
    """
    return tuple(natural_key(value) for value in values)


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
        # Through text_key, not a bare ("", ""): the two have to be the same
        # shape or comparing a present person against a missing one raises.
        return text_key("", "")
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
        house number sorts before the numbered ones on the same street --
        its `number` part is `(-1, "")`, which no real one can reach.
    """
    combined = " ".join(part for part in (street or "", house_number or "") if part).strip()
    match = _HOUSE_NUMBER_TAIL.match(combined)
    if match:
        # numeric_part, not int: a house number comes from the same external
        # web form as the rest of the address. See `numeric_part`.
        name, number, suffix = match.group(1), numeric_part(match.group(2)), match.group(3)
    else:
        name, number, suffix = combined, (-1, ""), ""
    # natural_key rather than fold_for_sort, so the street name and the
    # number's suffix read numbers the same way every other list does. The
    # house number itself is already split out and compared as an int.
    return (natural_key(name), number, natural_key(suffix)) + text_key(*rest)


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
