"""German-Swiss formatting for the two values this app is made of.

Layer-neutral, for the reason `app/format_size.py` and `app/sort_keys.py`
are: the PDF layer must not import from `app/gui`, and an amount has to read
the same on the screen and on the invoice.

**Money.** Amounts live as integer Rappen everywhere (see CLAUDE.md on money
handling) and were turned into text by hand at sixteen places in the
interface, each writing its own `f"{x / 100:.2f} CHF"`. Sixteen chances to
divide by the wrong number, and no thousands separator anywhere -- a figure
like `12345.60` is read wrongly at a glance, which matters on a Debitoren
page. `format_chf` is the one place that knows a Rappen is a hundredth and
that Switzerland groups with an apostrophe.

**Dates.** A date was written German on most pages and ISO on one
(`app/gui/pages/assignments.py` printed `2026-10-04` while its neighbours
printed `04.10.2026`). `format_date` settles it. What it deliberately does
*not* touch is an ISO string inside a `ui.input(..., type=date)` or in a sort
key: the browser's date field speaks ISO and sort keys compare text, so those
are not display at all.

Neither helper invents a value: `None` comes back as an em dash rather than
as `0.00` or today's date, because "not recorded" and "zero" are different
statements -- the same rule the Ausgewogenheit view follows when it prints
"—" instead of "0 %".
"""

from datetime import date, datetime
from typing import Optional, Union

#: What a missing value reads as. An em dash rather than "-", so it cannot be
#: mistaken for a minus sign in a column of amounts.
MISSING = "—"


def format_chf(rappen: Optional[int], *, with_unit: bool = False) -> str:
    """Write an amount of Rappen as Swiss francs.

    Args:
        rappen: The amount in integer Rappen, or `None`.
        with_unit: Append " CHF". Left off where a column header or a
            neighbouring label already says it.

    Returns:
        e.g. `"1'234.55"`, `"-20.00"`, or `MISSING` for `None`.
    """
    if rappen is None:
        return MISSING
    negative = rappen < 0
    francs, remainder = divmod(abs(int(rappen)), 100)
    # `f"{n:,}"` groups with commas; Switzerland groups with an apostrophe,
    # and the decimal separator stays a point (unlike German-German).
    text = f"{francs:,}".replace(",", "'") + f".{remainder:02d}"
    if negative:
        text = "-" + text
    return f"{text} CHF" if with_unit else text


def format_date(value: Union[date, datetime, str, None]) -> str:
    """Write a date the way the rest of the app writes it.

    Args:
        value: A `date`, a `datetime`, an ISO string as the database stores
            it, or `None`.

    Returns:
        e.g. `"04.10.2026"`, or `MISSING` when there is no date. An
        unparseable string is handed back unchanged rather than swallowed --
        a stored value nobody expected should be visible, not hidden.
    """
    if value is None or value == "":
        return MISSING
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value.strftime("%d.%m.%Y")
