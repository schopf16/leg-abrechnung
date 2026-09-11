"""Tests for calendar-quarter helpers, in particular data-availability lookup."""

from datetime import date

from app.domain.period import (
    latest_available_period,
    list_available_periods,
    month_bounds,
    month_label_de,
    months_in_quarter,
)
from app.models.reading import Reading, upsert_readings


def _insert_reading(db, metering_point_id: int, timestamp: str) -> None:
    """Insert a single reading for an existing MeteringPoint.

    Args:
        db: Database connection fixture.
        metering_point_id: MeteringPoint id to attach the reading to.
        timestamp: ISO-8601 timestamp for the reading.

    Returns:
        None.
    """
    upsert_readings(
        db,
        [
            Reading(
                metering_point_id=metering_point_id,
                timestamp=timestamp,
                direction="consumption",
                kwh=1.0,
                source="test",
            )
        ],
    )


def _make_metering_point(db) -> int:
    """Create a minimal site and MeteringPoint and return the MeteringPoint's id."""
    from app.models import metering_point as metering_point_repo
    from app.models import site as site_repo
    from app.models.metering_point import DIRECTION_CONSUMPTION, MeteringPoint
    from app.models.site import Site

    site_id = site_repo.create(
        db,
        Site(
            id=None,
            street="Musterstrasse",
            house_number="1",
            postal_code="3000",
            municipality="Bern",
            address_detail="",
            substation_area_id=None,
            created_at="",
        ),
    )
    return metering_point_repo.create(
        db,
        MeteringPoint(
            id=None,
            designation="CH-period-test",
            direction=DIRECTION_CONSUMPTION,
            site_id=site_id,
            leg_id=None,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
    )


def test_list_available_periods_empty_when_no_readings(db):
    """No readings at all means no available periods."""
    assert list_available_periods(db) == {}


def test_list_available_periods_groups_months_into_quarters(db):
    """Readings in different months of the same quarter collapse to one entry."""
    metering_point_id = _make_metering_point(db)
    _insert_reading(db, metering_point_id, "2025-01-15T12:00:00")  # Q1
    _insert_reading(db, metering_point_id, "2025-03-20T12:00:00")  # Q1
    _insert_reading(db, metering_point_id, "2025-07-01T00:00:00")  # Q3
    _insert_reading(db, metering_point_id, "2024-12-31T23:45:00")  # Q4 2024

    available = list_available_periods(db)

    assert available == {2025: {1, 3}, 2024: {4}}


def test_latest_available_period_picks_highest_year_and_quarter(db):
    """The latest period is the highest year, then highest quarter within it."""
    metering_point_id = _make_metering_point(db)
    _insert_reading(db, metering_point_id, "2024-05-01T00:00:00")  # 2024 Q2
    _insert_reading(db, metering_point_id, "2025-01-01T00:00:00")  # 2025 Q1
    _insert_reading(db, metering_point_id, "2025-11-01T00:00:00")  # 2025 Q4

    available = list_available_periods(db)
    assert latest_available_period(available) == (2025, 4)


def test_latest_available_period_none_when_empty():
    """An empty availability map has no latest period."""
    assert latest_available_period({}) is None


def test_months_in_quarter_returns_three_chronological_months():
    """Q1 covers January through March, Q4 covers October through December."""
    assert months_in_quarter(2025, 1) == [(2025, 1), (2025, 2), (2025, 3)]
    assert months_in_quarter(2025, 4) == [(2025, 10), (2025, 11), (2025, 12)]


def test_month_bounds_handles_leap_february():
    """February in a leap year runs through the 29th."""
    assert month_bounds(2024, 2) == (date(2024, 2, 1), date(2024, 2, 29))
    assert month_bounds(2025, 2) == (date(2025, 2, 1), date(2025, 2, 28))


def test_month_label_de_matches_expected_format():
    """The German label follows 'Monat (DD.MM-DD.MM)'."""
    assert month_label_de(2025, 1) == "Januar (01.01-31.01)"
    assert month_label_de(2025, 4) == "April (01.04-30.04)"
