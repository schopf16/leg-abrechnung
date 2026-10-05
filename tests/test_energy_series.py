"""Tests for the energy chart's series: consumption, feed-in, and how much of the production actually
found a taker inside the LEG."""

from datetime import datetime

import pytest

from app.domain import period
from app.domain.period import (
    GRANULARITY_DAY,
    GRANULARITY_HOUR,
    GRANULARITY_MONTH,
    GRANULARITY_QUARTER_HOUR,
    INTERVAL_MINUTES,
)
from app.domain.demo_data import SUMMER_QUARTER, create_demo_data
from app.domain.statistics import energy_series, energy_unit
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import site as site_repo
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.site import Site


def _leg(db, name: str = "LEG") -> int:
    """Create a LEG."""
    return leg_repo.create(
        db,
        Leg(
            id=None,
            name=name,
            note="",
            created_at="",
            production_capacity_percent=None,
            production_capacity_recorded_at=None,
        ),
    )


def _meter(db, leg_id: int, direction: str, suffix: str) -> int:
    """Create one metering point in a LEG."""
    site_id = site_repo.create(
        db,
        Site(
            id=None,
            street="Weg",
            house_number=suffix,
            postal_code="3063",
            municipality="Ittigen",
            address_detail="",
            substation_area_id=None,
            created_at="",
        ),
    )
    return metering_point_repo.create(
        db,
        MeteringPoint(
            id=None,
            designation=f"CH10180000000000000000000{suffix}",
            direction=direction,
            site_id=site_id,
            leg_id=leg_id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
    )


def _reading(db, metering_point_id: int, direction: str, moment: datetime, kwh: float) -> None:
    """Record one 15-minute reading."""
    db.execute(
        "INSERT INTO readings (metering_point_id, timestamp, direction, kwh, source) VALUES (?, ?, ?, ?, ?)",
        (metering_point_id, moment.isoformat(), direction, kwh, "test"),
    )
    db.commit()


def test_production_and_consumption_at_different_hours_share_nothing(db):
    """The test this module exists for."""
    leg_id = _leg(db)
    pv = _meter(db, leg_id, DIRECTION_FEED_IN, "01")
    house = _meter(db, leg_id, DIRECTION_CONSUMPTION, "02")
    day = datetime(2026, 7, 15)
    _reading(db, pv, DIRECTION_FEED_IN, day.replace(hour=9), 10.0)
    _reading(db, house, DIRECTION_CONSUMPTION, day.replace(hour=21), 10.0)

    window = period.window_for(GRANULARITY_DAY, day)
    bucket = next(b for b in energy_series(db, GRANULARITY_DAY, window) if b.start == day)

    assert bucket.feed_in_kwh == 10.0
    assert bucket.consumption_kwh == 10.0
    assert bucket.shared_kwh == 0.0, "zeitversetzt ist nicht geteilt"
    assert bucket.self_consumption_share == 0.0


def test_production_and_consumption_in_the_same_interval_do_share(db):
    """The other half: simultaneous is shared, up to the smaller of the two."""
    leg_id = _leg(db)
    pv = _meter(db, leg_id, DIRECTION_FEED_IN, "01")
    house = _meter(db, leg_id, DIRECTION_CONSUMPTION, "02")
    moment = datetime(2026, 7, 15, 12, 0)
    _reading(db, pv, DIRECTION_FEED_IN, moment, 4.0)
    _reading(db, house, DIRECTION_CONSUMPTION, moment, 3.0)

    window = period.window_for(GRANULARITY_DAY, moment)
    bucket = next(b for b in energy_series(db, GRANULARITY_DAY, window) if b.start.day == 15)

    assert bucket.shared_kwh == 3.0, "geteilt wird, was beide Seiten hergeben"
    assert bucket.self_consumption_share == pytest.approx(0.75)
    assert bucket.local_coverage_share == pytest.approx(1.0)


def test_two_legs_never_share_with_each_other(db):
    """Energy is shared *within* a LEG, never between neighbours."""
    moment = datetime(2026, 7, 15, 12, 0)
    producing = _leg(db, "LEG-Produktion")
    drawing = _leg(db, "LEG-Bezug")
    _reading(db, _meter(db, producing, DIRECTION_FEED_IN, "01"), DIRECTION_FEED_IN, moment, 5.0)
    _reading(db, _meter(db, drawing, DIRECTION_CONSUMPTION, "02"), DIRECTION_CONSUMPTION, moment, 5.0)

    window = period.window_for(GRANULARITY_DAY, moment)
    bucket = next(b for b in energy_series(db, GRANULARITY_DAY, window) if b.start.day == 15)

    assert bucket.feed_in_kwh == 5.0
    assert bucket.consumption_kwh == 5.0
    assert bucket.shared_kwh == 0.0, "zwei LEGs teilen nichts miteinander"


def test_the_totals_match_a_plain_sql_sum(db):
    """A figure that drifts from the readings is worse than no figure."""
    create_demo_data(db)
    window = period.window_for(GRANULARITY_MONTH, datetime(SUMMER_QUARTER[0], 8, 15))
    series = energy_series(db, GRANULARITY_MONTH, window)

    for bucket in series:
        month = bucket.start.strftime("%Y-%m")
        row = db.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN direction = 'consumption' THEN kwh ELSE 0 END), 0) AS c,
                   COALESCE(SUM(CASE WHEN direction <> 'consumption' THEN kwh ELSE 0 END), 0) AS f
            FROM readings WHERE substr(timestamp, 1, 7) = ?
            """,
            (month,),
        ).fetchone()
        assert bucket.consumption_kwh == pytest.approx(row["c"], abs=0.01), month
        assert bucket.feed_in_kwh == pytest.approx(row["f"], abs=0.01), month


def test_shared_never_exceeds_either_side(db):
    """An invariant of `min`, and a cheap guard against a sign or join slip."""
    create_demo_data(db)
    window = period.window_for(GRANULARITY_DAY, datetime(SUMMER_QUARTER[0], 8, 15))

    for bucket in energy_series(db, GRANULARITY_DAY, window):
        assert bucket.shared_kwh <= bucket.feed_in_kwh + 0.001
        assert bucket.shared_kwh <= bucket.consumption_kwh + 0.001


def test_empty_buckets_are_present_and_zero(db):
    """A day without readings is a gap in the line, not a shorter axis."""
    leg_id = _leg(db)
    pv = _meter(db, leg_id, DIRECTION_FEED_IN, "01")
    _reading(db, pv, DIRECTION_FEED_IN, datetime(2026, 7, 15, 12), 4.0)

    window = period.window_for(GRANULARITY_DAY, datetime(2026, 7, 15))
    series = energy_series(db, GRANULARITY_DAY, window)

    assert len(series) == 92, "das ganze Quartal, nicht nur der eine Tag"
    empty = next(b for b in series if b.start == datetime(2026, 7, 16))
    assert (empty.consumption_kwh, empty.feed_in_kwh, empty.shared_kwh) == (0.0, 0.0, 0.0)


def test_a_bucket_without_feed_in_has_no_share_rather_than_zero(db):
    """Night is not "0 % self-consumption" -- there was nothing to consume."""
    leg_id = _leg(db)
    house = _meter(db, leg_id, DIRECTION_CONSUMPTION, "01")
    _reading(db, house, DIRECTION_CONSUMPTION, datetime(2026, 7, 15, 2), 1.5)

    window = period.window_for(GRANULARITY_DAY, datetime(2026, 7, 15))
    bucket = next(b for b in energy_series(db, GRANULARITY_DAY, window) if b.start.day == 15)

    assert bucket.feed_in_kwh == 0.0
    assert bucket.self_consumption_share is None
    assert bucket.local_coverage_share == 0.0


def test_the_quarter_hour_view_is_power_and_the_rest_is_energy(db):
    """kW for the load curve, kWh for everything coarser."""
    leg_id = _leg(db)
    pv = _meter(db, leg_id, DIRECTION_FEED_IN, "01")
    house = _meter(db, leg_id, DIRECTION_CONSUMPTION, "02")
    moment = datetime(2026, 7, 15, 12, 0)
    _reading(db, pv, DIRECTION_FEED_IN, moment, 2.0)
    _reading(db, house, DIRECTION_CONSUMPTION, moment, 1.0)

    fine = period.window_for(GRANULARITY_QUARTER_HOUR, moment)
    bucket = next(b for b in energy_series(db, GRANULARITY_QUARTER_HOUR, fine) if b.start == moment)
    consumption, feed_in, shared = bucket.value_for(GRANULARITY_QUARTER_HOUR)

    factor = 60 / INTERVAL_MINUTES
    assert (consumption, feed_in, shared) == (1.0 * factor, 2.0 * factor, 1.0 * factor)
    assert energy_unit(GRANULARITY_QUARTER_HOUR) == "kW"

    # The hour view spans a whole week, so the bucket has to be picked by
    # its full moment -- "hour == 12" matches seven of them.
    coarse = period.window_for(GRANULARITY_HOUR, moment)
    hour_bucket = next(b for b in energy_series(db, GRANULARITY_HOUR, coarse) if b.start == moment)
    assert hour_bucket.value_for(GRANULARITY_HOUR) == (1.0, 2.0, 1.0)
    assert energy_unit(GRANULARITY_HOUR) == "kWh"


def test_restricting_to_one_leg_leaves_the_others_out(db):
    """The LEG filter has to reach the query, not just the label."""
    moment = datetime(2026, 7, 15, 12, 0)
    mine = _leg(db, "Meine")
    other = _leg(db, "Andere")
    _reading(db, _meter(db, mine, DIRECTION_FEED_IN, "01"), DIRECTION_FEED_IN, moment, 3.0)
    _reading(db, _meter(db, other, DIRECTION_FEED_IN, "02"), DIRECTION_FEED_IN, moment, 7.0)

    window = period.window_for(GRANULARITY_DAY, moment)
    everything = next(b for b in energy_series(db, GRANULARITY_DAY, window) if b.start.day == 15)
    just_mine = next(b for b in energy_series(db, GRANULARITY_DAY, window, leg_id=mine) if b.start.day == 15)

    assert everything.feed_in_kwh == 10.0
    assert just_mine.feed_in_kwh == 3.0


def test_readings_outside_the_window_are_not_counted(db):
    """The window is half-open: its last moment belongs to the next one."""
    leg_id = _leg(db)
    pv = _meter(db, leg_id, DIRECTION_FEED_IN, "01")
    _reading(db, pv, DIRECTION_FEED_IN, datetime(2026, 7, 15, 23, 45), 1.0)
    _reading(db, pv, DIRECTION_FEED_IN, datetime(2026, 7, 16, 0, 0), 99.0)

    window = period.window_for(GRANULARITY_QUARTER_HOUR, datetime(2026, 7, 15, 12))
    series = energy_series(db, GRANULARITY_QUARTER_HOUR, window)

    assert sum(b.feed_in_kwh for b in series) == 1.0, "Mitternacht gehört dem Folgetag"
