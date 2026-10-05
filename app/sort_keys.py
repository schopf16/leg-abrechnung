"""The sort keys every list in this app orders by, free of any layer."""

import re
import unicodedata
from typing import Any, Optional


def fold_for_sort(text: Optional[str]) -> str:
    """Fold text so it sorts the way a German reader expects."""
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
    """Turn a run of digits into a part that sorts numerically."""
    stripped = digits.lstrip("0") or "0"
    return (len(stripped), stripped)


def natural_key(text: Optional[str]) -> tuple:
    """Build a sort key that reads numbers inside text as numbers."""
    parts = _DIGIT_RUN.split(fold_for_sort(text))
    return tuple(numeric_part(part) if index % 2 else part for index, part in enumerate(parts))


def text_key(*values: Optional[str]) -> tuple[tuple, ...]:
    """Build a folded, number-aware sort key from several text parts."""
    return tuple(natural_key(value) for value in values)


def person_name_key(person: Any) -> tuple[str, ...]:
    """Build the surname-first sort key for a Person."""
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
    """Build a sort key for a postal address, ordering the house number numerically."""
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
    """Build a sort key for an optional number, keeping blanks together."""
    if value is None:
        return (1 if missing_last else -1, 0.0)
    return (0, float(value))
