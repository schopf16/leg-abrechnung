"""Tests for the Statistik page's trend aggregations."""

from datetime import date

from app.domain.period import trailing_months
from app.domain.statistics import monthly_energy_totals, monthly_growth_counts
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import site as site_repo
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.reading import Reading, upsert_readings
from app.models.site import Site


def test_trailing_months_lists_chronological_window_ending_at_reference():
    """`trailing_months` returns count months, oldest first, ending at the reference month."""
    months = trailing_months(date(2025, 3, 15), count=4)
    assert months == [(2024, 12), (2025, 1), (2025, 2), (2025, 3)]


def test_trailing_months_handles_year_boundary_for_single_month():
    """A 12-month window ending in January starts the previous February."""
    months = trailing_months(date(2025, 1, 1), count=12)
    assert months[0] == (2024, 2)
    assert months[-1] == (2025, 1)


def _make_site(db) -> int:
    return site_repo.create(
        db,
        Site(
            id=None, street="Musterstrasse", house_number="1", postal_code="3000", municipality="Bern", address_detail="",
            substation_area_id=None, created_at="",
        ),
    )


def _make_metering_point(db, designation: str, direction: str, site_id: int, leg_id=None) -> int:
    return metering_point_repo.create(
        db,
        MeteringPoint(
            id=None, designation=designation, direction=direction,
            site_id=site_id, leg_id=leg_id, pv_capacity_kwp=None,
            battery_capacity_kwh=None, created_at="",
        ),
    )


def _set_created_at(db, table: str, entity_id: int, when: date) -> None:
    """Overwrite a row's `created_at` for growth-bucketing tests."""
    db.execute(f"UPDATE {table} SET created_at = ? WHERE id = ?", (when.isoformat(), entity_id))
    db.commit()


def test_monthly_energy_totals_aggregates_by_month_and_direction(db):
    """consumption and feed-in readings are summed per calendar month."""
    site_id = _make_site(db)
    consumption_mp = _make_metering_point(db, "CH-B1", DIRECTION_CONSUMPTION, site_id)
    feed_in_mp = _make_metering_point(db, "CH-E1", DIRECTION_FEED_IN, site_id)

    upsert_readings(
        db,
        [
            Reading(metering_point_id=consumption_mp, timestamp="2025-06-01T00:00:00", direction="consumption", kwh=10.0, source="test"),
            Reading(metering_point_id=consumption_mp, timestamp="2025-06-01T00:15:00", direction="consumption", kwh=5.0, source="test"),
            Reading(metering_point_id=feed_in_mp, timestamp="2025-06-01T00:00:00", direction="feed_in", kwh=3.0, source="test"),
            Reading(metering_point_id=consumption_mp, timestamp="2025-05-01T00:00:00", direction="consumption", kwh=2.0, source="test"),
        ],
    )

    monthly = monthly_energy_totals(db, reference_date=date(2025, 6, 15), months=3)
    by_month = {(m.year, m.month): m for m in monthly}

    assert by_month[(2025, 6)].consumption_kwh == 15.0
    assert by_month[(2025, 6)].feed_in_kwh == 3.0
    assert by_month[(2025, 6)].balance_kwh == -12.0
    assert by_month[(2025, 5)].consumption_kwh == 2.0
    assert by_month[(2025, 4)].consumption_kwh == 0.0
    assert by_month[(2025, 4)].feed_in_kwh == 0.0


def test_monthly_energy_totals_filters_by_leg(db):
    """Passing a leg_id only counts readings from that LEG's metering points."""
    site_id = _make_site(db)
    leg_a = leg_repo.create(db, Leg(id=None, name="LEG A", note="", created_at=""))
    leg_b = leg_repo.create(db, Leg(id=None, name="LEG B", note="", created_at=""))
    mp_a = _make_metering_point(db, "CH-A", DIRECTION_CONSUMPTION, site_id, leg_id=leg_a)
    mp_b = _make_metering_point(db, "CH-B", DIRECTION_CONSUMPTION, site_id, leg_id=leg_b)

    upsert_readings(
        db,
        [
            Reading(metering_point_id=mp_a, timestamp="2025-06-01T00:00:00", direction="consumption", kwh=7.0, source="test"),
            Reading(metering_point_id=mp_b, timestamp="2025-06-01T00:00:00", direction="consumption", kwh=4.0, source="test"),
        ],
    )

    only_a = monthly_energy_totals(db, leg_id=leg_a, reference_date=date(2025, 6, 15), months=1)
    assert only_a[0].consumption_kwh == 7.0

    everything = monthly_energy_totals(db, leg_id=None, reference_date=date(2025, 6, 15), months=1)
    assert everything[0].consumption_kwh == 11.0


def test_monthly_growth_counts_are_cumulative(db):
    """A site created in an earlier month counts toward every later month too."""
    early_id = _make_site(db)
    _set_created_at(db, "site", early_id, date(2025, 1, 10))
    late_id = _make_site(db)
    _set_created_at(db, "site", late_id, date(2025, 3, 5))

    growth = monthly_growth_counts(db, reference_date=date(2025, 4, 30), months=4)
    by_month = {(g.year, g.month): g for g in growth}

    assert by_month[(2025, 1)].sites == 1
    assert by_month[(2025, 2)].sites == 1
    assert by_month[(2025, 3)].sites == 2
    assert by_month[(2025, 4)].sites == 2
