"""German-Swiss formatting for the two values this app is made of."""

from datetime import date, datetime
from typing import Optional, Union

#: What a missing value reads as. An em dash rather than "-", so it cannot be
#: mistaken for a minus sign in a column of amounts.
MISSING = "—"


def format_chf(rappen: Optional[int], *, with_unit: bool = False) -> str:
    """Write an amount of Rappen as Swiss francs."""
    if rappen is None:
        return MISSING
    negative = rappen < 0
    # `round`, not `int`: a stray float would be truncated by `int`, so
    # 1234.9 Rappen would print as 12.34 and lose a Rappen silently. Amounts
    # are integers by convention everywhere in this app (CLAUDE.md on money
    # handling) -- this is about what happens when one is not, and in an
    # invoice a wrong number is a wrong invoice to a real person.
    francs, remainder = divmod(abs(round(rappen)), 100)
    # `f"{n:,}"` groups with commas; Switzerland groups with an apostrophe,
    # and the decimal separator stays a point (unlike German-German).
    text = f"{francs:,}".replace(",", "'") + f".{remainder:02d}"
    if negative:
        text = "-" + text
    return f"{text} CHF" if with_unit else text


def format_date(value: Union[date, datetime, str, None]) -> str:
    """Write a date the way the rest of the app writes it."""
    if value is None or value == "":
        return MISSING
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value.strftime("%d.%m.%Y")
