"""Tests for the chart time axis: which window a resolution spans, and how
the arrows move it.

The window follows the resolution instead of being chosen separately, and
the point of that is a guarantee: no combination a user can pick produces
a chart the browser cannot draw. A year in quarter-hours would be 35'040
points. So the bucket counts below are not decoration -- they are the
guarantee, written down.
"""

from datetime import datetime, timedelta

import pytest

from app.domain import period
from app.domain.period import (
    GRANULARITIES,
    GRANULARITY_DAY,
    GRANULARITY_HOUR,
    GRANULARITY_MONTH,
    GRANULARITY_QUARTER_HOUR,
    GRANULARITY_YEAR,
    INTERVAL_MINUTES,
)


@pytest.mark.parametrize(
    "key, anchor, expected_buckets, expected_window",
    [
        (GRANULARITY_QUARTER_HOUR, datetime(2026, 9, 29, 14, 37), 96, "Dienstag, 29.09.2026"),
        (GRANULARITY_HOUR, datetime(2026, 9, 29, 14, 37), 168, "Woche vom 28.09.2026"),
        (GRANULARITY_DAY, datetime(2026, 9, 29, 14, 37), 92, "Q3 2026"),
        (GRANULARITY_MONTH, datetime(2026, 9, 29, 14, 37), 12, "2026"),
        (GRANULARITY_YEAR, datetime(2026, 9, 29, 14, 37), 10, "2017-2026"),
    ],
)
def test_each_resolution_spans_its_own_window(key, anchor, expected_buckets, expected_window):
    """Pick a resolution, get a window -- no third control to get wrong."""
    window = period.window_for(key, anchor)
    buckets = period.buckets_in(key, window)

    assert len(buckets) == expected_buckets
    assert period.window_label(key, window) == expected_window


def test_no_resolution_can_produce_an_undrawable_chart():
    """The guarantee the coupling exists for.

    Two hundred points is already a dense chart; a year in quarter-hours
    would be 35'040. If somebody ever widens a window, this fails before
    a user waits on a blank canvas.
    """
    anchor = datetime(2026, 7, 15, 12, 0)
    for granularity in GRANULARITIES:
        buckets = period.buckets_in(granularity.key, period.window_for(granularity.key, anchor))
        assert len(buckets) <= 200, f"{granularity.label}: {len(buckets)} Punkte"


def test_calendar_windows_are_half_open_and_meet_exactly():
    """Consecutive windows share no moment and leave no gap.

    A reading exactly at midnight must land in one day, not both and not
    neither -- the same rule `quarter_bounds` follows. The year view is
    excluded on purpose: it is a rolling ten years, so its windows overlap
    by nine (see `test_the_year_view_rolls_by_a_single_year`).
    """
    for granularity in GRANULARITIES:
        if granularity.key == GRANULARITY_YEAR:
            continue
        anchor = datetime(2026, 5, 20, 8, 0)
        _, end = period.window_for(granularity.key, anchor)
        next_start, _ = period.window_for(granularity.key, period.shift_anchor(granularity.key, anchor, 1))
        assert next_start == end, granularity.label


def test_the_year_view_rolls_by_a_single_year():
    """Regression test for a real design slip caught by these tests.

    The year window is ten years wide, and stepping it by its own width
    jumped a decade per click -- three clicks back from 2026 landed in
    1954, past every year that could ever hold data. It rolls by one year
    instead, which is what an arrow beside a ten-year chart means.
    """
    anchor = datetime(2026, 6, 1)

    back_one = period.window_for(GRANULARITY_YEAR, period.shift_anchor(GRANULARITY_YEAR, anchor, -1))
    back_three = period.window_for(GRANULARITY_YEAR, period.shift_anchor(GRANULARITY_YEAR, anchor, -3))

    assert period.window_label(GRANULARITY_YEAR, back_one) == "2016-2025"
    assert period.window_label(GRANULARITY_YEAR, back_three) == "2014-2023"


@pytest.mark.parametrize(
    "key, anchor, steps, expected",
    [
        (GRANULARITY_QUARTER_HOUR, datetime(2026, 1, 31, 12), 1, "Sonntag, 01.02.2026"),
        (GRANULARITY_QUARTER_HOUR, datetime(2026, 12, 31, 12), 1, "Freitag, 01.01.2027"),
        (GRANULARITY_QUARTER_HOUR, datetime(2026, 3, 1, 12), -1, "Samstag, 28.02.2026"),
        # A leap day exists and has to be reachable by stepping onto it.
        (GRANULARITY_QUARTER_HOUR, datetime(2024, 2, 28, 12), 1, "Donnerstag, 29.02.2024"),
        (GRANULARITY_HOUR, datetime(2026, 12, 30, 12), 1, "Woche vom 04.01.2027"),
        (GRANULARITY_DAY, datetime(2026, 9, 29, 12), 1, "Q4 2026"),
        (GRANULARITY_DAY, datetime(2026, 1, 15, 12), -1, "Q4 2025"),
        (GRANULARITY_DAY, datetime(2026, 9, 29, 12), -3, "Q4 2025"),
        (GRANULARITY_MONTH, datetime(2026, 6, 1, 12), 1, "2027"),
        (GRANULARITY_YEAR, datetime(2026, 6, 1, 12), -1, "2016-2025"),
    ],
)
def test_the_arrows_step_whole_windows(key, anchor, steps, expected):
    """Stepping is derived from the window bounds, not from a fixed number
    of days -- which is why a 28-day February and a 92-day quarter both
    land where a reader expects."""
    moved = period.shift_anchor(key, anchor, steps)

    assert period.window_label(key, period.window_for(key, moved)) == expected


def test_stepping_there_and_back_returns_to_the_same_window():
    """Otherwise the arrows would drift on every round trip."""
    for granularity in GRANULARITIES:
        anchor = datetime(2026, 3, 15, 10, 30)
        original = period.window_for(granularity.key, anchor)
        there = period.shift_anchor(granularity.key, anchor, 3)
        back = period.shift_anchor(granularity.key, there, -3)

        assert period.window_for(granularity.key, back) == original, granularity.label


def test_a_moment_falls_in_the_bucket_its_key_names():
    """`bucket_key` reads the stored ISO text; `bucket_key_of` reads a
    bucket start. They must agree, or every reading lands in no bucket."""
    anchor = datetime(2026, 7, 15, 9, 37, 42)
    for granularity in GRANULARITIES:
        window = period.window_for(granularity.key, anchor)
        buckets = period.buckets_in(granularity.key, window)
        keys = {period.bucket_key_of(granularity.key, start) for start in buckets}

        assert period.bucket_key(granularity.key, anchor.isoformat()) in keys, granularity.label


@pytest.mark.parametrize(
    "timestamp, expected",
    [
        ("2026-07-15T09:00:00", "2026-07-15T09:00"),
        ("2026-07-15T09:14:59", "2026-07-15T09:00"),
        ("2026-07-15T09:15:00", "2026-07-15T09:15"),
        ("2026-07-15T09:44:00", "2026-07-15T09:30"),
        ("2026-07-15T09:59:59", "2026-07-15T09:45"),
    ],
)
def test_quarter_hour_keys_snap_down_to_the_interval(timestamp, expected):
    """A reading is assigned to the interval it starts in, never rounded up."""
    assert period.bucket_key(GRANULARITY_QUARTER_HOUR, timestamp) == expected


def test_the_quarter_hour_bucket_is_the_apps_own_interval():
    """Derived from `INTERVAL_MINUTES`, not from a hardcoded 15.

    The same constant governs the kWh/kW conversion and the demo data; two
    places would be two places to drift.
    """
    window = period.window_for(GRANULARITY_QUARTER_HOUR, datetime(2026, 7, 15))
    buckets = period.buckets_in(GRANULARITY_QUARTER_HOUR, window)

    assert buckets[1] - buckets[0] == timedelta(minutes=INTERVAL_MINUTES)
    assert len(buckets) == 24 * 60 // INTERVAL_MINUTES


def test_buckets_come_from_the_window_not_from_the_data():
    """An empty stretch has to stay visible as a gap.

    Building the axis from whatever rows happen to exist would silently
    shorten it, and a week with a missing day would look like a shorter
    week rather than a missing day.
    """
    window = period.window_for(GRANULARITY_DAY, datetime(2026, 5, 20))
    buckets = period.buckets_in(GRANULARITY_DAY, window)

    assert buckets[0] == datetime(2026, 4, 1)
    assert buckets[-1] == datetime(2026, 6, 30)
    assert len(buckets) == 91


def test_an_unknown_resolution_is_refused_rather_than_guessed():
    """A typo in a key must not silently produce an empty chart."""
    for call in (
        lambda: period.window_for("wochen", datetime(2026, 1, 1)),
        lambda: period.buckets_in("wochen", (datetime(2026, 1, 1), datetime(2026, 1, 2))),
        lambda: period.bucket_key("wochen", "2026-01-01T00:00:00"),
        lambda: period.bucket_label("wochen", datetime(2026, 1, 1)),
    ):
        with pytest.raises(KeyError):
            call()
