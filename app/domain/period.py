"""Calendar-quarter helpers shared by the billing engine, demo data and GUI."""

import calendar
import sqlite3
from datetime import date, datetime
from typing import Optional

#: First calendar month of each quarter, 1-indexed (quarter -> month).
_QUARTER_START_MONTH = {1: 1, 2: 4, 3: 7, 4: 10}

#: Interval length used throughout the app, in minutes.
INTERVAL_MINUTES = 15

#: German month names, 1-indexed (index 0 unused).
MONTH_NAMES_DE = [
    "", "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


def quarter_bounds(year: int, quarter: int) -> tuple[datetime, datetime]:
    """Compute the half-open datetime range covering a calendar quarter.

    Args:
        year: Calendar year, e.g. 2025.
        quarter: Quarter number, 1 (Jan-Mar) to 4 (Oct-Dec).

    Returns:
        A `(start, end_exclusive)` tuple of naive local datetimes, where
        `start` is the first interval's start (00:00 on the first day) and
        `end_exclusive` is midnight of the day after the quarter ends.

    Raises:
        ValueError: If `quarter` is not between 1 and 4.
    """
    if quarter not in _QUARTER_START_MONTH:
        raise ValueError(f"Quarter must be 1-4, got {quarter}")
    start_month = _QUARTER_START_MONTH[quarter]
    start = datetime(year, start_month, 1)
    if quarter == 4:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, start_month + 3, 1)
    return start, end


def quarter_label(year: int, quarter: int) -> str:
    """Format a calendar quarter as a short German label.

    Args:
        year: Calendar year.
        quarter: Quarter number, 1 to 4.

    Returns:
        A label such as "Q3 2025".
    """
    return f"Q{quarter} {year}"


def quarter_of(moment: datetime) -> tuple[int, int]:
    """Determine the calendar year and quarter a given moment falls into.

    Args:
        moment: The datetime to classify.

    Returns:
        A `(year, quarter)` tuple.
    """
    return moment.year, (moment.month - 1) // 3 + 1


def list_available_periods(connection: sqlite3.Connection) -> dict[int, set[int]]:
    """Determine which (year, quarter) combinations actually have readings.

    Used by the GUI to only ever offer year/quarter combinations that have
    data, instead of letting the user pick a period that can never produce
    a result.

    Args:
        connection: Open SQLite connection.

    Returns:
        A dict mapping calendar year to the set of quarter numbers (1-4)
        for which at least one reading exists. Empty if there are no
        readings at all.
    """
    rows = connection.execute(
        "SELECT DISTINCT substr(timestamp, 1, 4) AS yr, substr(timestamp, 6, 2) AS mo FROM readings"
    ).fetchall()
    periods: dict[int, set[int]] = {}
    for row in rows:
        year = int(row["yr"])
        month = int(row["mo"])
        quarter = (month - 1) // 3 + 1
        periods.setdefault(year, set()).add(quarter)
    return periods


def latest_available_period(
    available: dict[int, set[int]]
) -> Optional[tuple[int, int]]:
    """Pick the most recent (year, quarter) that has data, as a GUI default.

    Args:
        available: Result of `list_available_periods`.

    Returns:
        The `(year, quarter)` with the highest year and, within that year,
        the highest quarter -- or `None` if `available` is empty.
    """
    if not available:
        return None
    latest_year = max(available)
    latest_quarter = max(available[latest_year])
    return latest_year, latest_quarter


def months_in_quarter(year: int, quarter: int) -> list[tuple[int, int]]:
    """List the three calendar months making up a quarter.

    Args:
        year: Calendar year.
        quarter: Quarter number, 1 to 4.

    Returns:
        `(year, month)` pairs in chronological order, e.g. for Q1 2025:
        `[(2025, 1), (2025, 2), (2025, 3)]`.
    """
    start_month = _QUARTER_START_MONTH[quarter]
    return [(year, start_month + offset) for offset in range(3)]


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """Compute the first and last calendar day of a month.

    Args:
        year: Calendar year.
        month: Calendar month, 1 to 12.

    Returns:
        A `(first_day, last_day)` tuple, both inclusive.
    """
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def trailing_months(end: date, count: int = 12) -> list[tuple[int, int]]:
    """List `count` consecutive calendar months ending with `end`'s month.

    Used by `app.domain.statistics` to build a fixed-width trailing window
    (e.g. "the last 12 months") regardless of which months actually have
    data -- months with nothing recorded still appear, with zero values.

    Args:
        end: Reference date; its `(year, month)` is the last entry.
        count: Number of months to list.

    Returns:
        `(year, month)` pairs in chronological order (oldest first).
    """
    months = []
    year, month = end.year, end.month
    for _ in range(count):
        months.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(months))


def month_label_de(year: int, month: int) -> str:
    """Format a calendar month as a German label with its date range.

    Args:
        year: Calendar year.
        month: Calendar month, 1 to 12.

    Returns:
        A label such as "Januar (01.01-31.01)".
    """
    first_day, last_day = month_bounds(year, month)
    return (
        f"{MONTH_NAMES_DE[month]} "
        f"({first_day.strftime('%d.%m')}-{last_day.strftime('%d.%m')})"
    )
