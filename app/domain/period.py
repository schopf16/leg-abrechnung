"""Calendar-quarter helpers shared by the billing engine, demo data and GUI."""

from datetime import datetime

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
