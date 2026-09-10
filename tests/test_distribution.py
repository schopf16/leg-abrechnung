"""Tests for the 15-minute distribution engine and its edge cases.

Covers the project brief's required cases: `P(t) = 0`, `C(t) = 0`,
production-limited (`P > C`) and consumption-limited (`P < C`) sharing,
attribution across a mid-quarter Person move, and the requirement that
sharing never crosses a LEG boundary.
"""

import uuid
from datetime import date, datetime, timedelta

import pytest

from app.domain.distribution import LegNotAssignedError, compute_quarter_distribution
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import assignment as assignment_repo
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.person import Person
from app.models.reading import Reading, upsert_readings
from app.models.site import Site
from app.models.assignment import Assignment

YEAR, QUARTER = 2025, 1  # Jan-Mar 2025, used as a fast, controlled sandbox.


def _person(db, name: str) -> int:
    """Create a person and return its id.

    Args:
        db: Database connection fixture.
        name: Person name.

    Returns:
        The new person's id.
    """
    return person_repo.create(
        db,
        Person(
            id=None, salutation="", company="", first_name=name, last_name="",
            contact_email="", contact_phone="",
            billing_street="", billing_house_number="", billing_postal_code="",
            billing_city="", billing_country="CH",
            iban="", customer_number=None, bkw_customer_number=None, paper_invoice=False, active=True, created_at="",
        ),
    )


def _leg(db) -> int:
    """Create a LEG with a unique name and return its id."""
    return leg_repo.create(
        db,
        Leg(id=None, name=f"Testkreis-{uuid.uuid4().hex[:8]}", note="", created_at=""),
    )


def _site(db) -> int:
    """Create a minimal site (no substation area needed for these tests) and
    return its id.

    Args:
        db: Database connection fixture.

    Returns:
        The new site's id.
    """
    return site_repo.create(
        db,
        Site(
            id=None, street="Musterstrasse", house_number="1", postal_code="3000", municipality="Bern", address_detail="",
            substation_area_id=None, created_at="",
        ),
    )


def _metering_point(
    db, designation: str, direction: str, site_id: int, leg_id: "int | None | str" = "auto"
) -> int:
    """Create a MeteringPoint and return its id.

    Args:
        db: Database connection fixture.
        designation: Business key.
        direction: Measurement direction.
        site_id: Foreign key of the site the MeteringPoint belongs to.
        leg_id: LEG to assign. Defaults to a freshly created one (most
            tests just need *a* valid LEG, not to control which one);
            pass `None` explicitly to test the unassigned case.

    Returns:
        The new MeteringPoint's id.
    """
    if leg_id == "auto":
        leg_id = _leg(db)
    return metering_point_repo.create(
        db,
        MeteringPoint(
            id=None, designation=designation,
            direction=direction, site_id=site_id, leg_id=leg_id,
            pv_capacity_kwp=None, battery_capacity_kwh=None, created_at="",
        ),
    )


def _assign(db, metering_point_id: int, person_id: int, valid_from: date, valid_to: date | None = None) -> None:
    """Create a Assignment.

    Args:
        db: Database connection fixture.
        metering_point_id: MeteringPoint to assign.
        person_id: Person to assign it to.
        valid_from: Start of validity.
        valid_to: End of validity, or `None` for open-ended.

    Returns:
        None.
    """
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_id, metering_point_id=metering_point_id,
            valid_from=valid_from, valid_to=valid_to, created_at="",
        ),
    )


def _reading(db, metering_point_id: int, moment: datetime, direction: str, kwh: float) -> None:
    """Insert a single reading.

    Args:
        db: Database connection fixture.
        metering_point_id: MeteringPoint the reading belongs to.
        moment: Interval start.
        direction: "consumption" or "feed_in".
        kwh: Energy for the interval.

    Returns:
        None.
    """
    upsert_readings(
        db,
        [Reading(metering_point_id=metering_point_id, timestamp=moment.isoformat(), direction=direction, kwh=kwh, source="test")],
    )


def test_zero_production_yields_zero_sharing(db):
    """If P(t) = 0, no energy is shared even though consumption is nonzero."""
    leg_id = _leg(db)
    site = _site(db)
    consumer = _person(db, "Consumer")
    producer = _person(db, "Producer")
    consumption_mp = _metering_point(db, "M-C1", DIRECTION_CONSUMPTION, site, leg_id)
    production_mp = _metering_point(db, "M-P1", DIRECTION_FEED_IN, site, leg_id)
    _assign(db, consumption_mp, consumer, date(YEAR, 1, 1))
    _assign(db, production_mp, producer, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, consumption_mp, t, "bezug", 2.0)
    _reading(db, production_mp, t, "einspeisung", 0.0)

    result = compute_quarter_distribution(db, leg_id, YEAR, QUARTER)

    assert result.total_consumed_local_kwh() == 0.0
    assert result.total_produced_local_kwh() == 0.0


def test_zero_consumption_yields_zero_sharing(db):
    """If C(t) = 0, no energy is shared even though production is nonzero."""
    leg_id = _leg(db)
    site = _site(db)
    producer = _person(db, "Producer")
    production_mp = _metering_point(db, "M-P1", DIRECTION_FEED_IN, site, leg_id)
    _assign(db, production_mp, producer, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, production_mp, t, "einspeisung", 5.0)

    result = compute_quarter_distribution(db, leg_id, YEAR, QUARTER)

    assert result.total_produced_local_kwh() == 0.0
    assert result.person_results == {}


def test_production_surplus_limits_sharing_to_consumption(db):
    """When P(t) > C(t), sharing is capped at consumption (S = C)."""
    leg_id = _leg(db)
    site = _site(db)
    consumer = _person(db, "Consumer")
    producer = _person(db, "Producer")
    consumption_mp = _metering_point(db, "M-C1", DIRECTION_CONSUMPTION, site, leg_id)
    production_mp = _metering_point(db, "M-P1", DIRECTION_FEED_IN, site, leg_id)
    _assign(db, consumption_mp, consumer, date(YEAR, 1, 1))
    _assign(db, production_mp, producer, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, consumption_mp, t, "bezug", 4.0)
    _reading(db, production_mp, t, "einspeisung", 10.0)

    result = compute_quarter_distribution(db, leg_id, YEAR, QUARTER)

    assert result.person_results[consumer].consumed_local_kwh == pytest.approx(4.0)
    assert result.person_results[producer].produced_local_kwh == pytest.approx(4.0)


def test_production_deficit_splits_proportionally_across_consumers(db):
    """When P(t) < C(t), consumers share the deficit-limited energy proportionally."""
    leg_id = _leg(db)
    site = _site(db)
    consumer_a = _person(db, "Consumer A")
    consumer_b = _person(db, "Consumer B")
    producer = _person(db, "Producer")
    mp_a = _metering_point(db, "M-C1", DIRECTION_CONSUMPTION, site, leg_id)
    mp_b = _metering_point(db, "M-C2", DIRECTION_CONSUMPTION, site, leg_id)
    production_mp = _metering_point(db, "M-P1", DIRECTION_FEED_IN, site, leg_id)
    _assign(db, mp_a, consumer_a, date(YEAR, 1, 1))
    _assign(db, mp_b, consumer_b, date(YEAR, 1, 1))
    _assign(db, production_mp, producer, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, mp_a, t, "bezug", 6.0)
    _reading(db, mp_b, t, "bezug", 2.0)
    _reading(db, production_mp, t, "einspeisung", 3.0)

    result = compute_quarter_distribution(db, leg_id, YEAR, QUARTER)

    # C(t) = 8, P(t) = 3, S(t) = 3. Consumer A gets 6/8 of it, Consumer B 2/8.
    assert result.person_results[consumer_a].consumed_local_kwh == pytest.approx(2.25)
    assert result.person_results[consumer_b].consumed_local_kwh == pytest.approx(0.75)
    assert result.person_results[producer].produced_local_kwh == pytest.approx(3.0)
    assert result.total_consumed_local_kwh() == pytest.approx(result.total_produced_local_kwh())


def test_mid_period_move_splits_metering_point_between_two_persons(db):
    """A MeteringPoint reassigned mid-quarter attributes readings to the correct person."""
    leg_id = _leg(db)
    site = _site(db)
    tenant_before = _person(db, "Vormieter")
    tenant_after = _person(db, "Nachmieter")
    producer = _person(db, "Producer")
    consumption_mp = _metering_point(db, "M-C1", DIRECTION_CONSUMPTION, site, leg_id)
    production_mp = _metering_point(db, "M-P1", DIRECTION_FEED_IN, site, leg_id)

    move_day = date(YEAR, 2, 1)
    _assign(db, consumption_mp, tenant_before, date(YEAR, 1, 1), move_day - timedelta(days=1))
    _assign(db, consumption_mp, tenant_after, move_day)
    _assign(db, production_mp, producer, date(YEAR, 1, 1))

    before_move = datetime(YEAR, 1, 15, 12, 0)
    after_move = datetime(YEAR, 2, 15, 12, 0)
    _reading(db, consumption_mp, before_move, "bezug", 5.0)
    _reading(db, consumption_mp, after_move, "bezug", 3.0)
    _reading(db, production_mp, before_move, "einspeisung", 5.0)
    _reading(db, production_mp, after_move, "einspeisung", 3.0)

    result = compute_quarter_distribution(db, leg_id, YEAR, QUARTER)

    assert result.person_results[tenant_before].consumed_local_kwh == pytest.approx(5.0)
    assert result.person_results[tenant_after].consumed_local_kwh == pytest.approx(3.0)
    assert result.unassigned_kwh == 0.0


def test_unassigned_metering_point_reading_is_tracked_not_dropped(db):
    """A reading for a MeteringPoint with no covering Assignment is reported, not billed."""
    leg_id = _leg(db)
    site = _site(db)
    producer = _person(db, "Producer")
    consumption_mp = _metering_point(db, "M-C1", DIRECTION_CONSUMPTION, site, leg_id)
    production_mp = _metering_point(db, "M-P1", DIRECTION_FEED_IN, site, leg_id)
    # consumption_mp is intentionally never assigned to anyone.
    _assign(db, production_mp, producer, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, consumption_mp, t, "bezug", 4.0)
    _reading(db, production_mp, t, "einspeisung", 4.0)

    result = compute_quarter_distribution(db, leg_id, YEAR, QUARTER)

    # The production side is correctly attributed to the producer; only the
    # consumption side (unassigned MeteringPoint) is reported as unassigned.
    assert result.person_results[producer].produced_local_kwh == pytest.approx(4.0)
    assert result.person_results[producer].consumed_local_kwh == 0.0
    assert result.unassigned_kwh == pytest.approx(4.0)


def test_monthly_breakdown_sums_to_quarter_total_and_covers_all_months(db):
    """Each person's monthly dicts cover all 3 quarter months and sum to the total."""
    leg_id = _leg(db)
    site = _site(db)
    consumer = _person(db, "Consumer")
    producer = _person(db, "Producer")
    consumption_mp = _metering_point(db, "M-C1", DIRECTION_CONSUMPTION, site, leg_id)
    production_mp = _metering_point(db, "M-P1", DIRECTION_FEED_IN, site, leg_id)
    _assign(db, consumption_mp, consumer, date(YEAR, 1, 1))
    _assign(db, production_mp, producer, date(YEAR, 1, 1))

    # January and March get readings; February gets none (must still show as 0).
    _reading(db, consumption_mp, datetime(YEAR, 1, 15, 12, 0), "bezug", 4.0)
    _reading(db, production_mp, datetime(YEAR, 1, 15, 12, 0), "einspeisung", 4.0)
    _reading(db, consumption_mp, datetime(YEAR, 3, 20, 12, 0), "bezug", 2.0)
    _reading(db, production_mp, datetime(YEAR, 3, 20, 12, 0), "einspeisung", 2.0)

    result = compute_quarter_distribution(db, leg_id, YEAR, QUARTER)
    consumer_result = result.person_results[consumer]
    producer_result = result.person_results[producer]

    assert set(consumer_result.consumed_by_month.keys()) == {1, 2, 3}
    assert consumer_result.consumed_by_month[1] == pytest.approx(4.0)
    assert consumer_result.consumed_by_month[2] == pytest.approx(0.0)
    assert consumer_result.consumed_by_month[3] == pytest.approx(2.0)
    assert sum(consumer_result.consumed_by_month.values()) == pytest.approx(
        consumer_result.consumed_local_kwh
    )

    assert set(producer_result.produced_by_month.keys()) == {1, 2, 3}
    assert sum(producer_result.produced_by_month.values()) == pytest.approx(
        producer_result.produced_local_kwh
    )


def test_metering_point_without_leg_raises_error(db):
    """A MeteringPoint with readings but no LEG assigned blocks the whole run."""
    unrelated_leg_id = _leg(db)
    site = _site(db)
    producer = _person(db, "Producer")
    production_mp = _metering_point(db, "M-P1", DIRECTION_FEED_IN, site, leg_id=None)
    _assign(db, production_mp, producer, date(YEAR, 1, 1))
    _reading(db, production_mp, datetime(YEAR, 1, 15, 12, 0), "einspeisung", 5.0)

    with pytest.raises(LegNotAssignedError, match="M-P1"):
        compute_quarter_distribution(db, unrelated_leg_id, YEAR, QUARTER)


def test_metering_point_without_readings_does_not_block_run(db):
    """A MeteringPoint with no LEG but also no readings this quarter is not an obstacle."""
    unused_site = _site(db)
    _metering_point(db, "M-UNUSED", DIRECTION_CONSUMPTION, unused_site, leg_id=None)  # no readings on it
    leg_id = _leg(db)
    site = _site(db)
    consumer = _person(db, "Consumer")
    producer = _person(db, "Producer")
    consumption_mp = _metering_point(db, "M-C1", DIRECTION_CONSUMPTION, site, leg_id)
    production_mp = _metering_point(db, "M-P1", DIRECTION_FEED_IN, site, leg_id)
    _assign(db, consumption_mp, consumer, date(YEAR, 1, 1))
    _assign(db, production_mp, producer, date(YEAR, 1, 1))
    _reading(db, consumption_mp, datetime(YEAR, 1, 15, 12, 0), "bezug", 4.0)
    _reading(db, production_mp, datetime(YEAR, 1, 15, 12, 0), "einspeisung", 4.0)

    result = compute_quarter_distribution(db, leg_id, YEAR, QUARTER)
    assert result.person_results[consumer].consumed_local_kwh == pytest.approx(4.0)


def test_distribution_scoped_to_one_leg_excludes_other_legs_metering_points(db):
    """Two persons on different LEGs never share, even with matching P(t)/C(t)."""
    leg_a = _leg(db)
    leg_b = _leg(db)
    site_a = _site(db)
    site_b = _site(db)
    consumer = _person(db, "Consumer")
    producer = _person(db, "Producer")
    consumption_mp = _metering_point(db, "M-C1", DIRECTION_CONSUMPTION, site_a, leg_a)
    production_mp = _metering_point(db, "M-P1", DIRECTION_FEED_IN, site_b, leg_b)
    _assign(db, consumption_mp, consumer, date(YEAR, 1, 1))
    _assign(db, production_mp, producer, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, consumption_mp, t, "bezug", 4.0)
    _reading(db, production_mp, t, "einspeisung", 4.0)

    # Computed for leg_a: only the consumer's MeteringPoint is in scope, the
    # producer's MeteringPoint (on leg_b) never even enters the computation.
    result_a = compute_quarter_distribution(db, leg_a, YEAR, QUARTER)
    assert consumer not in result_a.person_results or result_a.person_results[consumer].consumed_local_kwh == 0.0
    assert producer not in result_a.person_results
    assert result_a.total_consumed_local_kwh() == 0.0

    # Computed for leg_b: symmetric -- only the producer's MeteringPoint is in scope.
    result_b = compute_quarter_distribution(db, leg_b, YEAR, QUARTER)
    assert producer not in result_b.person_results or result_b.person_results[producer].produced_local_kwh == 0.0
    assert consumer not in result_b.person_results
    assert result_b.total_produced_local_kwh() == 0.0


def test_two_legs_each_share_correctly_within_themselves(db):
    """Two independent LEGs each compute correct, isolated local sharing."""
    leg_a = _leg(db)
    leg_b = _leg(db)
    site_a = _site(db)
    site_b = _site(db)
    consumer_a = _person(db, "Consumer A")
    producer_a = _person(db, "Producer A")
    consumer_b = _person(db, "Consumer B")
    producer_b = _person(db, "Producer B")
    consumption_mp_a = _metering_point(db, "A-C1", DIRECTION_CONSUMPTION, site_a, leg_a)
    production_mp_a = _metering_point(db, "A-P1", DIRECTION_FEED_IN, site_a, leg_a)
    consumption_mp_b = _metering_point(db, "B-C1", DIRECTION_CONSUMPTION, site_b, leg_b)
    production_mp_b = _metering_point(db, "B-P1", DIRECTION_FEED_IN, site_b, leg_b)
    _assign(db, consumption_mp_a, consumer_a, date(YEAR, 1, 1))
    _assign(db, production_mp_a, producer_a, date(YEAR, 1, 1))
    _assign(db, consumption_mp_b, consumer_b, date(YEAR, 1, 1))
    _assign(db, production_mp_b, producer_b, date(YEAR, 1, 1))

    t = datetime(YEAR, 1, 15, 12, 0)
    _reading(db, consumption_mp_a, t, "bezug", 4.0)
    _reading(db, production_mp_a, t, "einspeisung", 4.0)
    _reading(db, consumption_mp_b, t, "bezug", 10.0)
    _reading(db, production_mp_b, t, "einspeisung", 6.0)

    result_a = compute_quarter_distribution(db, leg_a, YEAR, QUARTER)
    assert result_a.person_results[consumer_a].consumed_local_kwh == pytest.approx(4.0)
    assert result_a.person_results[producer_a].produced_local_kwh == pytest.approx(4.0)
    assert consumer_b not in result_a.person_results
    assert producer_b not in result_a.person_results

    # LEG B is deficit-limited (P=6 < C=10): only 6 kWh is shared.
    result_b = compute_quarter_distribution(db, leg_b, YEAR, QUARTER)
    assert result_b.person_results[consumer_b].consumed_local_kwh == pytest.approx(6.0)
    assert result_b.person_results[producer_b].produced_local_kwh == pytest.approx(6.0)
    assert consumer_a not in result_b.person_results
    assert producer_a not in result_b.person_results
