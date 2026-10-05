"""Calendar-quarter helpers shared by the billing engine, demo data and GUI, plus the time-axis
vocabulary the Statistik charts are drawn on."""

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
    """Compute the half-open datetime range covering a calendar quarter."""
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
    """Format a calendar quarter as a short German label."""
    return f"Q{quarter} {year}"


def quarter_of(moment: datetime) -> tuple[int, int]:
    """Determine the calendar year and quarter a given moment falls into."""
    return moment.year, (moment.month - 1) // 3 + 1


def last_completed_quarter(today: Optional[date] = None) -> tuple[int, int]:
    """The most recent quarter that has already ended."""
    reference = today or date.today()
    year, quarter = reference.year, (reference.month - 1) // 3 + 1
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def list_available_periods(connection: sqlite3.Connection) -> dict[int, set[int]]:
    """Determine which (year, quarter) combinations actually have readings."""
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
    """Pick the most recent (year, quarter) that has data, as a GUI default."""
    if not available:
        return None
    latest_year = max(available)
    latest_quarter = max(available[latest_year])
    return latest_year, latest_quarter


def months_in_quarter(year: int, quarter: int) -> list[tuple[int, int]]:
    """List the three calendar months making up a quarter."""
    start_month = _QUARTER_START_MONTH[quarter]
    return [(year, start_month + offset) for offset in range(3)]


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """Compute the first and last calendar day of a month."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def trailing_months(end: date, count: int = 12) -> list[tuple[int, int]]:
    """List `count` consecutive calendar months ending with `end`'s month."""
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
    """Format a calendar month as a German label with its date range."""
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
    """One selectable x-axis resolution and the window that comes with it."""

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
    """Look up one resolution by its key."""
    return GRANULARITIES_BY_KEY[key]


def window_for(key: str, anchor: datetime) -> tuple[datetime, datetime]:
    """The half-open window one resolution covers around a moment."""
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
    """Move the anchor one window onward or back, for the navigation arrows."""
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
    """Every bucket start in a window, in order."""
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
    """The bucket one stored reading timestamp belongs to."""
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
    """The same bucket key, for a bucket start rather than a reading."""
    return bucket_key(key, start.isoformat())


def bucket_label(key: str, start: datetime) -> str:
    """The German axis label for one bucket."""
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
    """Name the window on screen, so the arrows have something to move."""
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


def shift_window_one_year(window: tuple[datetime, datetime]) -> tuple[datetime, datetime]:
    """The same window, one year earlier."""

    def back(moment: datetime) -> datetime:
        try:
            return moment.replace(year=moment.year - 1)
        except ValueError:
            # 29 February in a year that has none.
            return moment.replace(year=moment.year - 1, day=28)

    start, end = window
    return back(start), back(end)
