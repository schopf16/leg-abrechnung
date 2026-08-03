"""Tests for participant/meter/assignment CRUD and consistency warnings."""

from datetime import date

import pytest

from app.models import assignment as assignment_repo
from app.models import meter as meter_repo
from app.models import participant as participant_repo
from app.models.assignment import MeterAssignment
from app.models.meter import Meter
from app.models.participant import Participant


def _make_participant(name: str = "Test Teilnehmer") -> Participant:
    """Build an unpersisted `Participant` for use in tests.

    Args:
        name: Name to assign.

    Returns:
        A `Participant` with `id=None`.
    """
    return Participant(
        id=None,
        name=name,
        address_street="Musterstrasse 1",
        address_zip="3000",
        address_city="Bern",
        address_country="CH",
        iban="CH9300762011623852957",
        email="test@example.ch",
        created_at="",
    )


def _make_meter(metering_point_id: str = "CH1234567890123456789012345", role: str = "bezug") -> Meter:
    """Build an unpersisted `Meter` for use in tests.

    Args:
        metering_point_id: Business key to assign.
        role: Meter role.

    Returns:
        A `Meter` with `id=None`.
    """
    return Meter(
        id=None,
        metering_point_id=metering_point_id,
        label="Testzähler",
        building_address="Musterstrasse 1, 3000 Bern",
        role=role,
        created_at="",
    )


def test_participant_crud_roundtrip(db):
    """Creating, fetching, updating and deleting a participant all work."""
    participant_id = participant_repo.create(db, _make_participant())
    fetched = participant_repo.get(db, participant_id)
    assert fetched is not None
    assert fetched.name == "Test Teilnehmer"

    fetched.name = "Geänderter Name"
    participant_repo.update(db, fetched)
    assert participant_repo.get(db, participant_id).name == "Geänderter Name"

    participant_repo.delete(db, participant_id)
    assert participant_repo.get(db, participant_id) is None


def test_meter_rejects_unknown_role(db):
    """Creating a meter with an invalid role raises ValueError."""
    with pytest.raises(ValueError):
        meter_repo.create(db, _make_meter(role="unbekannt"))


def test_meter_metering_point_id_is_unique(db):
    """Two meters cannot share the same metering point id."""
    meter_repo.create(db, _make_meter(metering_point_id="CH1"))
    with pytest.raises(Exception):
        meter_repo.create(db, _make_meter(metering_point_id="CH1"))


def test_meter_role_categorization():
    """Consumption and production roles are categorized correctly."""
    assert _make_meter(role="bezug").is_consumption
    assert _make_meter(role="bezug_fix").is_consumption
    assert _make_meter(role="bezug_geschaltet").is_consumption
    assert not _make_meter(role="bezug").is_production
    assert _make_meter(role="produktion").is_production
    assert not _make_meter(role="produktion").is_consumption


def test_assignment_covers_respects_open_and_closed_ranges():
    """`MeterAssignment.covers` handles open-ended and bounded periods."""
    open_ended = MeterAssignment(
        id=1, meter_id=1, participant_id=1,
        valid_from=date(2025, 1, 1), valid_to=None, created_at="",
    )
    assert open_ended.covers(_dt(2025, 6, 1))
    assert not open_ended.covers(_dt(2024, 12, 31))

    bounded = MeterAssignment(
        id=2, meter_id=1, participant_id=2,
        valid_from=date(2025, 1, 1), valid_to=date(2025, 3, 31), created_at="",
    )
    assert bounded.covers(_dt(2025, 2, 1))
    assert not bounded.covers(_dt(2025, 4, 1))


def _dt(year: int, month: int, day: int):
    """Build a naive `datetime` at midnight for the given date.

    Args:
        year: Calendar year.
        month: Calendar month.
        day: Calendar day.

    Returns:
        A `datetime` at 00:00 on the given date.
    """
    from datetime import datetime

    return datetime(year, month, day)


def test_find_warnings_detects_gap(db):
    """A gap between two assignment periods is reported."""
    participant_a = participant_repo.create(db, _make_participant("A"))
    participant_b = participant_repo.create(db, _make_participant("B"))
    meter_id = meter_repo.create(db, _make_meter())

    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_a,
            valid_from=date(2025, 1, 1), valid_to=date(2025, 1, 31), created_at="",
        ),
    )
    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_b,
            valid_from=date(2025, 2, 5), valid_to=None, created_at="",
        ),
    )

    warnings = assignment_repo.find_warnings(db, meter_id)
    assert len(warnings) == 1
    assert warnings[0].kind == "gap"


def test_find_warnings_detects_overlap(db):
    """Overlapping assignment periods are reported."""
    participant_a = participant_repo.create(db, _make_participant("A"))
    participant_b = participant_repo.create(db, _make_participant("B"))
    meter_id = meter_repo.create(db, _make_meter())

    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_a,
            valid_from=date(2025, 1, 1), valid_to=date(2025, 2, 15), created_at="",
        ),
    )
    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_b,
            valid_from=date(2025, 2, 1), valid_to=None, created_at="",
        ),
    )

    warnings = assignment_repo.find_warnings(db, meter_id)
    assert len(warnings) == 1
    assert warnings[0].kind == "overlap"


def test_find_warnings_none_for_consecutive_periods(db):
    """Back-to-back assignments with no gap or overlap raise no warnings."""
    participant_a = participant_repo.create(db, _make_participant("A"))
    participant_b = participant_repo.create(db, _make_participant("B"))
    meter_id = meter_repo.create(db, _make_meter())

    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_a,
            valid_from=date(2025, 1, 1), valid_to=date(2025, 8, 15), created_at="",
        ),
    )
    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_b,
            valid_from=date(2025, 8, 16), valid_to=None, created_at="",
        ),
    )

    assert assignment_repo.find_warnings(db, meter_id) == []
