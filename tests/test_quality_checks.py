"""Tests for plausibility/consistency checks: assignment and reading gaps."""

from datetime import date, datetime, timedelta

from app.domain.quality_checks import check_assignment_consistency, check_reading_completeness
from app.models import assignment as assignment_repo
from app.models import meter as meter_repo
from app.models import participant as participant_repo
from app.models.assignment import MeterAssignment
from app.models.meter import Meter
from app.models.participant import Participant
from app.models.reading import Reading, upsert_readings

YEAR, QUARTER = 2025, 1


def _participant(db, name: str = "P") -> int:
    """Create a participant and return its id."""
    return participant_repo.create(
        db,
        Participant(
            id=None, name=name, address_street="", address_zip="", address_city="",
            address_country="CH", iban="", email="", created_at="",
        ),
    )


def _meter(db, metering_point_id: str = "CH-Q1") -> int:
    """Create a "bezug" meter and return its id."""
    return meter_repo.create(
        db,
        Meter(
            id=None, metering_point_id=metering_point_id, label=metering_point_id,
            building_address="", role="bezug", created_at="",
        ),
    )


def test_check_assignment_consistency_reports_gaps_across_all_meters(db):
    """A gap in one meter's assignment history is surfaced by the aggregate check."""
    participant_id = _participant(db)
    meter_id = _meter(db)
    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_id,
            valid_from=date(2025, 1, 1), valid_to=date(2025, 1, 10), created_at="",
        ),
    )
    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_id,
            valid_from=date(2025, 1, 20), valid_to=None, created_at="",
        ),
    )

    warnings = check_assignment_consistency(db)
    assert any(w.category == "zuordnung_luecke" for w in warnings)


def test_check_assignment_consistency_clean_history_has_no_warnings(db):
    """A single open-ended assignment produces no warnings."""
    participant_id = _participant(db)
    meter_id = _meter(db)
    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_id,
            valid_from=date(2025, 1, 1), valid_to=None, created_at="",
        ),
    )
    assert check_assignment_consistency(db) == []


def test_check_reading_completeness_flags_days_with_missing_values(db):
    """A day with fewer than 96 readings, while the meter is assigned, is flagged."""
    participant_id = _participant(db)
    meter_id = _meter(db)
    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_id,
            valid_from=date(YEAR, 1, 1), valid_to=None, created_at="",
        ),
    )

    # Only 4 of the expected 96 readings for Jan 15th.
    day = datetime(YEAR, 1, 15)
    readings = [
        Reading(meter_id=meter_id, timestamp=(day + timedelta(minutes=15 * i)).isoformat(), direction="bezug", kwh=0.1, source="test")
        for i in range(4)
    ]
    upsert_readings(db, readings)

    warnings = check_reading_completeness(db, YEAR, QUARTER)
    assert any("2025-01-15" in w.message for w in warnings)


def test_check_reading_completeness_ignores_days_without_assignment(db):
    """A meter that was never assigned to anyone produces no completeness warnings."""
    meter_id = _meter(db)
    # No assignment created at all.
    warnings = check_reading_completeness(db, YEAR, QUARTER)
    assert warnings == []


def test_check_reading_completeness_no_warning_for_fully_covered_day(db):
    """A day with exactly 96 readings is not flagged."""
    participant_id = _participant(db)
    meter_id = _meter(db)
    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_id,
            valid_from=date(YEAR, 1, 15), valid_to=date(YEAR, 1, 15), created_at="",
        ),
    )
    day = datetime(YEAR, 1, 15)
    readings = [
        Reading(meter_id=meter_id, timestamp=(day + timedelta(minutes=15 * i)).isoformat(), direction="bezug", kwh=0.1, source="test")
        for i in range(96)
    ]
    upsert_readings(db, readings)

    warnings = check_reading_completeness(db, YEAR, QUARTER)
    assert not any("2025-01-15" in w.message for w in warnings)
