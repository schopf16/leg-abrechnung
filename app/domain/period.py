"""Calendar-quarter helpers shared by the billing engine, demo data and GUI,
plus the time-axis vocabulary the Statistik charts are drawn on.

The axis part lives here, and not in `app/gui/`, for the same reason
`app/sort_keys.py` does: it must be usable without NiceGUI. A chart and a
later CSV export of the same figures have to cut the time into identical
buckets, or the export will quietly disagree with the picture it came
from. `app/gui/time_axis.py` only adds the controls on top.

**Timestamps in this app are naive local time**, as BKW delivers them, and
nothing converts or tags them. A "day" is therefore 00:00 to 00:00, always
96 intervals -- which is exactly right except on the two days a year that
really have 92 or 100 because the clocks moved. Those days will plot one
hour short or long. Handling it properly would mean carrying a timezone
through the readings table, which is a much larger change than the
distortion justifies.
"""

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

#: First calendar month of each quarter, 1-indexed (quarter -> month).
_QUARTER_START_MONTH = {1: 1, 2: 4, 3: 7, 4: 10}

#: Interval length used throughout the app, in minutes.
INTERVAL_MINUTES = 15

#: German month names, 1-indexed (index 0 unused).
MONTH_NAMES_DE = [
    "",
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
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


def last_completed_quarter(today: Optional[date] = None) -> tuple[int, int]:
    """The most recent quarter that has already ended.

    The sensible default when starting a billing run: a quarter still
    running cannot be billed, and the one before it is almost always what
    is meant. Independent of what is in the database -- a billing run is
    started *before* its readings arrive, not after.

    Args:
        today: Day to measure from, defaulting to today.

    Returns:
        A `(year, quarter)` tuple.
    """
    reference = today or date.today()
    year, quarter = reference.year, (reference.month - 1) // 3 + 1
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


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


def latest_available_period(available: dict[int, set[int]]) -> Optional[tuple[int, int]]:
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
    return f"{MONTH_NAMES_DE[month]} ({first_day.strftime('%d.%m')}-{last_day.strftime('%d.%m')})"


#: The resolutions a chart's x-axis can be drawn at, finest first. The key
#: is what the select stores; `label` is what it shows.
GRANULARITY_QUARTER_HOUR = "quarter_hour"
GRANULARITY_HOUR = "hour"
GRANULARITY_DAY = "day"
GRANULARITY_MONTH = "month"
GRANULARITY_YEAR = "year"

#: How many years the "Jahr" resolution looks back over. Fixed rather than
#: "everything there is", so it needs no data to decide its own width.
#: With at most a few years of readings it shows everything anyway.
_YEARS_IN_WINDOW = 10


@dataclass(frozen=True)
class Granularity:
    """One selectable x-axis resolution and the window that comes with it.

    The window **follows** the resolution instead of being chosen
    separately. The alternative -- free from/to dates next to a free
    resolution -- lets somebody ask for a year in quarter-hours, which is
    35'040 points: a chart the browser cannot draw and a query nobody
    wants to wait for. Coupling them means no combination exists that
    produces an unusable picture.

    Attributes:
        key: Stable identifier, stored by the select.
        label: German name of the resolution ("Viertelstunde").
        window_label: German name of the window it spans ("Tag"), shown
            beside the navigation arrows.
        buckets_per_window: Roughly how many points a full window holds --
            documentation and a sanity bound for the tests, not something
            the code divides by. Months and quarters vary in length.
    """

    key: str
    label: str
    window_label: str
    buckets_per_window: int


#: Every resolution, finest first. A page passes the subset it offers to
#: `app.gui.time_axis.render_time_axis`; the receivables charts, for
#: instance, have no use for quarter-hours.
GRANULARITIES: list[Granularity] = [
    Granularity(GRANULARITY_QUARTER_HOUR, "Viertelstunde", "Tag", 96),
    Granularity(GRANULARITY_HOUR, "Stunde", "Woche", 168),
    Granularity(GRANULARITY_DAY, "Tag", "Quartal", 92),
    Granularity(GRANULARITY_MONTH, "Monat", "Jahr", 12),
    Granularity(GRANULARITY_YEAR, "Jahr", f"{_YEARS_IN_WINDOW} Jahre", _YEARS_IN_WINDOW),
]

GRANULARITIES_BY_KEY: dict[str, Granularity] = {g.key: g for g in GRANULARITIES}


def granularity(key: str) -> Granularity:
    """Look up one resolution by its key.

    Args:
        key: One of the `GRANULARITY_*` constants.

    Returns:
        The matching `Granularity`.

    Raises:
        KeyError: If no resolution has this key.
    """
    return GRANULARITIES_BY_KEY[key]


def window_for(key: str, anchor: datetime) -> tuple[datetime, datetime]:
    """The half-open window one resolution covers around a moment.

    Args:
        key: One of the `GRANULARITY_*` constants.
        anchor: Any moment inside the wanted window.

    Returns:
        `(start, end_exclusive)`, snapped to the natural boundary --
        midnight for a day, Monday for a week, the first of the quarter,
        1 January for a year. Half-open, like `quarter_bounds`, so
        consecutive windows never share a moment.
    """
    if key == GRANULARITY_QUARTER_HOUR:
        start = datetime(anchor.year, anchor.month, anchor.day)
        return start, start + timedelta(days=1)
    if key == GRANULARITY_HOUR:
        midnight = datetime(anchor.year, anchor.month, anchor.day)
        start = midnight - timedelta(days=midnight.weekday())
        return start, start + timedelta(days=7)
    if key == GRANULARITY_DAY:
        return quarter_bounds(*quarter_of(anchor))
    if key == GRANULARITY_MONTH:
        return datetime(anchor.year, 1, 1), datetime(anchor.year + 1, 1, 1)
    if key == GRANULARITY_YEAR:
        first_year = anchor.year - _YEARS_IN_WINDOW + 1
        return datetime(first_year, 1, 1), datetime(anchor.year + 1, 1, 1)
    raise KeyError(key)


def shift_anchor(key: str, anchor: datetime, steps: int) -> datetime:
    """Move the anchor one window onward or back, for the navigation arrows.

    Four of the five resolutions show a **calendar** window -- this day,
    this week, this quarter, this year -- so one step is one whole window
    and consecutive windows meet exactly. The year view is the exception:
    it is a **rolling** ten years, and stepping it by its own width would
    jump a decade at a time, past every year that has data. It therefore
    rolls by a single year, and its windows overlap by nine.

    Args:
        key: One of the `GRANULARITY_*` constants.
        anchor: The current anchor.
        steps: How many steps to move; negative goes back.

    Returns:
        A moment inside the neighbouring window. Derived from the window's
        own bounds rather than by adding a fixed number of days, so
        stepping past a 28-day February or a 92-day quarter lands where a
        reader expects.
    """
    if key == GRANULARITY_YEAR:
        return datetime(anchor.year + steps, 1, 1)

    start, end = window_for(key, anchor)
    moved = start
    for _ in range(abs(steps)):
        if steps > 0:
            moved = window_for(key, end)[0]
        else:
            moved = window_for(key, moved - timedelta(microseconds=1))[0]
        start, end = window_for(key, moved)
    return moved


def buckets_in(key: str, window: tuple[datetime, datetime]) -> list[datetime]:
    """Every bucket start in a window, in order.

    Generated from the window rather than from the data, so a period with
    no readings still appears -- as a gap at the right place, not by
    silently shortening the axis.

    Args:
        key: One of the `GRANULARITY_*` constants.
        window: `(start, end_exclusive)` as returned by `window_for`.

    Returns:
        The bucket start moments, oldest first.
    """
    start, end = window
    starts: list[datetime] = []
    current = start
    while current < end:
        starts.append(current)
        if key == GRANULARITY_QUARTER_HOUR:
            current += timedelta(minutes=INTERVAL_MINUTES)
        elif key == GRANULARITY_HOUR:
            current += timedelta(hours=1)
        elif key == GRANULARITY_DAY:
            current += timedelta(days=1)
        elif key == GRANULARITY_MONTH:
            current = (
                datetime(current.year + 1, 1, 1)
                if current.month == 12
                else datetime(current.year, current.month + 1, 1)
            )
        elif key == GRANULARITY_YEAR:
            current = datetime(current.year + 1, 1, 1)
        else:
            raise KeyError(key)
    return starts


def bucket_key(key: str, timestamp: str) -> str:
    """The bucket one stored reading timestamp belongs to.

    Works on the ISO text as stored (`"2026-07-01T00:15:00"`) rather than
    parsing every row: a quarter of a million readings per quarter makes
    the difference between a chart that appears and one you wait for.

    Args:
        key: One of the `GRANULARITY_*` constants.
        timestamp: The reading's ISO-8601 timestamp.

    Returns:
        A string that is equal for two timestamps in the same bucket, and
        matches `bucket_key_of(key, bucket_start)`.
    """
    if key == GRANULARITY_YEAR:
        return timestamp[:4]
    if key == GRANULARITY_MONTH:
        return timestamp[:7]
    if key == GRANULARITY_DAY:
        return timestamp[:10]
    if key == GRANULARITY_HOUR:
        return timestamp[:13]
    if key == GRANULARITY_QUARTER_HOUR:
        minute = int(timestamp[14:16])
        return f"{timestamp[:14]}{minute - minute % INTERVAL_MINUTES:02d}"
    raise KeyError(key)


def bucket_key_of(key: str, start: datetime) -> str:
    """The same bucket key, for a bucket start rather than a reading.

    Args:
        key: One of the `GRANULARITY_*` constants.
        start: A bucket start from `buckets_in`.

    Returns:
        The key `bucket_key` would produce for a reading in that bucket.
    """
    return bucket_key(key, start.isoformat())


def bucket_label(key: str, start: datetime) -> str:
    """The German axis label for one bucket.

    Args:
        key: One of the `GRANULARITY_*` constants.
        start: The bucket's start moment.

    Returns:
        A label short enough to sit under an axis tick -- "08:15" inside a
        day, "Mo 14.09." inside a week, "14.09." inside a quarter, "Sep"
        inside a year, "2026" across years.
    """
    if key == GRANULARITY_QUARTER_HOUR:
        return start.strftime("%H:%M")
    if key == GRANULARITY_HOUR:
        return f"{_WEEKDAYS_DE[start.weekday()]} {start.strftime('%H:%M')}"
    if key == GRANULARITY_DAY:
        return start.strftime("%d.%m.")
    if key == GRANULARITY_MONTH:
        return MONTH_NAMES_DE[start.month][:3]
    if key == GRANULARITY_YEAR:
        return str(start.year)
    raise KeyError(key)


def window_label(key: str, window: tuple[datetime, datetime]) -> str:
    """Name the window on screen, so the arrows have something to move.

    Args:
        key: One of the `GRANULARITY_*` constants.
        window: `(start, end_exclusive)` as returned by `window_for`.

    Returns:
        E.g. `"Dienstag, 29.09.2026"`, `"Woche vom 28.09.2026"`,
        `"Q3 2026"`, `"2026"` or `"2017-2026"`.
    """
    start, end = window
    last_day = end - timedelta(days=1)
    if key == GRANULARITY_QUARTER_HOUR:
        return f"{_WEEKDAYS_LONG_DE[start.weekday()]}, {start.strftime('%d.%m.%Y')}"
    if key == GRANULARITY_HOUR:
        return f"Woche vom {start.strftime('%d.%m.%Y')}"
    if key == GRANULARITY_DAY:
        return quarter_label(*quarter_of(start))
    if key == GRANULARITY_MONTH:
        return str(start.year)
    if key == GRANULARITY_YEAR:
        return f"{start.year}-{last_day.year}"
    raise KeyError(key)


#: Short German weekday names, Monday first (matches `date.weekday()`).
_WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

#: The same, written out.
_WEEKDAYS_LONG_DE = [
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
]
