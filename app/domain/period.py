"""Calendar-quarter helpers shared by the billing engine, demo data and GUI."""

import sqlite3
from datetime import datetime
from typing import Optional

#: First calendar month of each quarter, 1-indexed (quarter -> month).
_QUARTER_START_MONTH = {1: 1, 2: 4, 3: 7, 4: 10}

#: Interval length used throughout the app, in minutes.
INTERVAL_MINUTES = 15


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
