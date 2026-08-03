"""Tests for the 15-minute distribution engine and its edge cases.

Covers the project brief's required cases: `P(t) = 0`, `C(t) = 0`,
production-limited (`P > C`) and consumption-limited (`P < C`) sharing,
and attribution across a mid-quarter participant move.
"""

from datetime import date, datetime, timedelta

import pytest

from app.domain.distribution import compute_quarter_distribution
from app.models import assignment as assignment_repo
from app.models import meter as meter_repo
from app.models import participant as participant_repo
from app.models.assignment import MeterAssignment
from app.models.meter import Meter
from app.models.participant import Participant
from app.models.reading import Reading, upsert_readings

YEAR, QUARTER = 2025, 1  # Jan-Mar 2025, used as a fast, controlled sandbox.


def _participant(db, name: str) -> int:
    """Create a participant and return its id.

    Args:
        db: Database connection fixture.
        name: Participant name.

    Returns:
        The new participant's id.
    """
    return participant_repo.create(
        db,
        Participant(
            id=None, name=name, address_street="", address_zip="", address_city="",
            address_country="CH", iban="", email="", created_at="",
        ),
    )


def _meter(db, metering_point_id: str, role: str) -> int:
    """Create a meter and return its id.

    Args:
        db: Database connection fixture.
        metering_point_id: Business key.
        role: Meter role.

    Returns:
        The new meter's id.
    """
    return meter_repo.create(
        db,
        Meter(
            id=None, metering_point_id=metering_point_id, label=metering_point_id,
            building_address="", role=role, created_at="",
        ),
    )


def _assign(db, meter_id: int, participant_id: int, valid_from: date, valid_to: date | None = None) -> None:
    """Create a meter assignment.

    Args:
        db: Database connection fixture.
        meter_id: Meter to assign.
        participant_id: Participant to assign it to.
        valid_from: Start of validity.
        valid_to: End of validity, or `None` for open-ended.

    Returns:
        None.
    """
    assignment_repo.create(
        db,
        MeterAssignment(
            id=None, meter_id=meter_id, participant_id=participant_id,
            valid_from=valid_from, valid_to=valid_to, created_at="",
        ),
    )


def _reading(db, meter_id: int, moment: datetime, direction: str, kwh: float) -> None:
    """Insert a single reading.

    Args:
        db: Database connection fixture.
        meter_id: Meter the reading belongs to.
        moment: Interval start.
        direction: "bezug" or "produktion".
        kwh: Energy for the interval.

    Returns:
        None.
    """
    upsert_readings(
        db,
        [Reading(meter_id=meter_id, timestamp=moment.isoformat(), direction=direction, kwh=kwh, source="test")],
    )


def test_zero_production_yields_zero_sharing(db):
    """If P(t) = 0, no energy is shared even though consumption is nonzero."""
    consumer = _participant(db, "Consumer")
    producer = _participant(db, "Producer")
    consumption_meter = _meter(db, "M-C1", "bezug")
    production_meter = _meter(db, "M-P1", "produktion")
    _assign(db, consumption_meter, consumer, date(YEAR, 1, 1))
    _assign(db, production_meter, producer, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, consumption_meter, t, "bezug", 2.0)
    _reading(db, production_meter, t, "produktion", 0.0)

    result = compute_quarter_distribution(db, YEAR, QUARTER)

    assert result.total_consumed_local_kwh() == 0.0
    assert result.total_produced_local_kwh() == 0.0


def test_zero_consumption_yields_zero_sharing(db):
    """If C(t) = 0, no energy is shared even though production is nonzero."""
    producer = _participant(db, "Producer")
    production_meter = _meter(db, "M-P1", "produktion")
    _assign(db, production_meter, producer, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, production_meter, t, "produktion", 5.0)

    result = compute_quarter_distribution(db, YEAR, QUARTER)

    assert result.total_produced_local_kwh() == 0.0
    assert result.participant_results == {}


def test_production_surplus_limits_sharing_to_consumption(db):
    """When P(t) > C(t), sharing is capped at consumption (S = C)."""
    consumer = _participant(db, "Consumer")
    producer = _participant(db, "Producer")
    consumption_meter = _meter(db, "M-C1", "bezug")
    production_meter = _meter(db, "M-P1", "produktion")
    _assign(db, consumption_meter, consumer, date(YEAR, 1, 1))
    _assign(db, production_meter, producer, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, consumption_meter, t, "bezug", 4.0)
    _reading(db, production_meter, t, "produktion", 10.0)

    result = compute_quarter_distribution(db, YEAR, QUARTER)

    assert result.participant_results[consumer].consumed_local_kwh == pytest.approx(4.0)
    assert result.participant_results[producer].produced_local_kwh == pytest.approx(4.0)


def test_production_deficit_splits_proportionally_across_consumers(db):
    """When P(t) < C(t), consumers share the deficit-limited energy proportionally."""
    consumer_a = _participant(db, "Consumer A")
    consumer_b = _participant(db, "Consumer B")
    producer = _participant(db, "Producer")
    meter_a = _meter(db, "M-C1", "bezug")
    meter_b = _meter(db, "M-C2", "bezug")
    production_meter = _meter(db, "M-P1", "produktion")
    _assign(db, meter_a, consumer_a, date(YEAR, 1, 1))
    _assign(db, meter_b, consumer_b, date(YEAR, 1, 1))
    _assign(db, production_meter, producer, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, meter_a, t, "bezug", 6.0)
    _reading(db, meter_b, t, "bezug", 2.0)
    _reading(db, production_meter, t, "produktion", 3.0)

    result = compute_quarter_distribution(db, YEAR, QUARTER)

    # C(t) = 8, P(t) = 3, S(t) = 3. Consumer A gets 6/8 of it, Consumer B 2/8.
    assert result.participant_results[consumer_a].consumed_local_kwh == pytest.approx(2.25)
    assert result.participant_results[consumer_b].consumed_local_kwh == pytest.approx(0.75)
    assert result.participant_results[producer].produced_local_kwh == pytest.approx(3.0)
    assert result.total_consumed_local_kwh() == pytest.approx(result.total_produced_local_kwh())


def test_mid_period_move_splits_meter_between_two_participants(db):
    """A meter reassigned mid-quarter attributes readings to the correct participant."""
    tenant_before = _participant(db, "Vormieter")
    tenant_after = _participant(db, "Nachmieter")
    producer = _participant(db, "Producer")
    consumption_meter = _meter(db, "M-C1", "bezug")
    production_meter = _meter(db, "M-P1", "produktion")

    move_day = date(YEAR, 2, 1)
    _assign(db, consumption_meter, tenant_before, date(YEAR, 1, 1), move_day - timedelta(days=1))
    _assign(db, consumption_meter, tenant_after, move_day)
    _assign(db, production_meter, producer, date(YEAR, 1, 1))

    before_move = datetime(YEAR, 1, 15, 12, 0)
    after_move = datetime(YEAR, 2, 15, 12, 0)
    _reading(db, consumption_meter, before_move, "bezug", 5.0)
    _reading(db, consumption_meter, after_move, "bezug", 3.0)
    _reading(db, production_meter, before_move, "produktion", 5.0)
    _reading(db, production_meter, after_move, "produktion", 3.0)

    result = compute_quarter_distribution(db, YEAR, QUARTER)

    assert result.participant_results[tenant_before].consumed_local_kwh == pytest.approx(5.0)
    assert result.participant_results[tenant_after].consumed_local_kwh == pytest.approx(3.0)
    assert result.unassigned_kwh == 0.0


def test_unassigned_meter_reading_is_tracked_not_dropped(db):
    """A reading for a meter with no covering assignment is reported, not billed."""
    producer = _participant(db, "Producer")
    consumption_meter = _meter(db, "M-C1", "bezug")
    production_meter = _meter(db, "M-P1", "produktion")
    # consumption_meter is intentionally never assigned to anyone.
    _assign(db, production_meter, producer, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, consumption_meter, t, "bezug", 4.0)
    _reading(db, production_meter, t, "produktion", 4.0)

    result = compute_quarter_distribution(db, YEAR, QUARTER)

    # The production side is correctly attributed to the producer; only the
    # consumption side (unassigned meter) is reported as unassigned.
    assert result.participant_results[producer].produced_local_kwh == pytest.approx(4.0)
    assert result.participant_results[producer].consumed_local_kwh == 0.0
    assert result.unassigned_kwh == pytest.approx(4.0)
