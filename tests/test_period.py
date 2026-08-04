"""Tests for calendar-quarter helpers, in particular data-availability lookup."""

from app.domain.period import latest_available_period, list_available_periods
from app.models.reading import Reading, upsert_readings


def _insert_reading(db, meter_id: int, timestamp: str) -> None:
    """Insert a single reading with an arbitrary meter id (no FK needed
    for these SQL-only queries since foreign_keys enforcement would
    require a real meter row -- so callers must create one first).

    Args:
        db: Database connection fixture.
        meter_id: Meter id to attach the reading to.
        timestamp: ISO-8601 timestamp for the reading.

    Returns:
        None.
    """
    upsert_readings(
        db,
        [Reading(meter_id=meter_id, timestamp=timestamp, direction="bezug", kwh=1.0, source="test")],
    )


def _make_meter(db) -> int:
    """Create a minimal meter and return its id."""
    from app.models import meter as meter_repo
    from app.models.meter import Meter

    return meter_repo.create(
        db,
        Meter(id=None, metering_point_id="CH-period-test", label="x", building_address="", role="bezug", created_at=""),
    )


def test_list_available_periods_empty_when_no_readings(db):
    """No readings at all means no available periods."""
    assert list_available_periods(db) == {}


def test_list_available_periods_groups_months_into_quarters(db):
    """Readings in different months of the same quarter collapse to one entry."""
    meter_id = _make_meter(db)
    _insert_reading(db, meter_id, "2025-01-15T12:00:00")  # Q1
    _insert_reading(db, meter_id, "2025-03-20T12:00:00")  # Q1
    _insert_reading(db, meter_id, "2025-07-01T00:00:00")  # Q3
    _insert_reading(db, meter_id, "2024-12-31T23:45:00")  # Q4 2024

    available = list_available_periods(db)

    assert available == {2025: {1, 3}, 2024: {4}}


def test_latest_available_period_picks_highest_year_and_quarter(db):
    """The latest period is the highest year, then highest quarter within it."""
    meter_id = _make_meter(db)
    _insert_reading(db, meter_id, "2024-05-01T00:00:00")  # 2024 Q2
    _insert_reading(db, meter_id, "2025-01-01T00:00:00")  # 2025 Q1
    _insert_reading(db, meter_id, "2025-11-01T00:00:00")  # 2025 Q4

    available = list_available_periods(db)
    assert latest_available_period(available) == (2025, 4)


def test_latest_available_period_none_when_empty():
    """An empty availability map has no latest period."""
    assert latest_available_period({}) is None
