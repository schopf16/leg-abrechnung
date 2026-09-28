"""Tests for the installed PV/battery totals shown on the overview.

These figures are informational -- nothing bills from them -- which is
exactly why they must not quietly disagree with the metering points they
claim to summarise, or look complete while several meters carry no value at
all. Same reasoning as `tests/test_quarter_statistics.py`.
"""

from app.domain.demo_data import create_demo_data
from app.domain.statistics import CAPACITY_PLAUSIBLE_MAX, installed_capacity_totals
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import site as site_repo
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.site import Site


def _site(db) -> int:
    """Create a site to hang metering points off.

    Args:
        db: Database connection fixture.

    Returns:
        The new site's id.
    """
    return site_repo.create(
        db,
        Site(
            id=None,
            street="Neuweg",
            house_number="1",
            postal_code="3000",
            municipality="Bern",
            address_detail="",
            substation_area_id=None,
            created_at="",
        ),
    )


def _meter(db, site_id, *, direction=DIRECTION_FEED_IN, pv=None, battery=None, suffix="01") -> int:
    """Create one metering point with the given capacities.

    Args:
        db: Database connection fixture.
        site_id: Site to attach it to.
        direction: Consumption or feed-in.
        pv: `pv_capacity_kwp`, or `None`.
        battery: `battery_capacity_kwh`, or `None`.
        suffix: Two digits making the designation unique.

    Returns:
        The new metering point's id.
    """
    return metering_point_repo.create(
        db,
        MeteringPoint(
            id=None,
            designation=f"CH10180000000000000000000{suffix}",
            direction=direction,
            site_id=site_id,
            leg_id=None,
            pv_capacity_kwp=pv,
            battery_capacity_kwh=battery,
            created_at="",
        ),
    )


def test_the_sums_match_a_plain_sql_sum_over_the_plausible_rows(db):
    """A figure that drifts from the metering points is worse than none."""
    create_demo_data(db)
    totals = installed_capacity_totals(db)

    expected_pv = db.execute(
        """
        SELECT COALESCE(SUM(pv_capacity_kwp), 0) AS total, COUNT(pv_capacity_kwp) AS n
        FROM metering_point
        WHERE direction = 'feed_in' AND pv_capacity_kwp > 0 AND pv_capacity_kwp < ?
        """,
        (CAPACITY_PLAUSIBLE_MAX,),
    ).fetchone()
    expected_battery = db.execute(
        """
        SELECT COALESCE(SUM(battery_capacity_kwh), 0) AS total, COUNT(battery_capacity_kwh) AS n
        FROM metering_point
        WHERE battery_capacity_kwh > 0 AND battery_capacity_kwh < ?
        """,
        (CAPACITY_PLAUSIBLE_MAX,),
    ).fetchone()

    assert totals.pv_kwp == round(expected_pv["total"], 2)
    assert totals.pv_counted == expected_pv["n"]
    assert totals.battery_kwh == round(expected_battery["total"], 2)
    assert totals.battery_counted == expected_battery["n"]


def test_expected_counts_every_feed_in_meter_even_without_a_value(db):
    """The "26 von 29" case: the sum is incomplete and has to say so."""
    site_id = _site(db)
    _meter(db, site_id, pv=10.0, suffix="01")
    _meter(db, site_id, pv=8.0, suffix="02")
    _meter(db, site_id, pv=None, suffix="03")

    totals = installed_capacity_totals(db)
    assert totals.pv_kwp == 18.0
    assert totals.pv_counted == 2
    assert totals.pv_expected == 3, "ein Messpunkt ohne Angabe zählt zur Erwartung"


def test_none_zero_and_an_absurd_value_are_all_excluded(db):
    """Only a real figure may move the total."""
    site_id = _site(db)
    _meter(db, site_id, pv=10.0, suffix="01")
    _meter(db, site_id, pv=0.0, suffix="02")
    _meter(db, site_id, pv=18500.0, suffix="03")
    _meter(db, site_id, pv=None, suffix="04")

    totals = installed_capacity_totals(db)
    assert totals.pv_kwp == 10.0
    assert totals.pv_counted == 1
    assert len(totals.implausible) == 2, "der Nullwert und der Tippfehler werden benannt"
    assert all(designation.startswith("CH1018") for designation in totals.implausible)


def test_a_pv_figure_on_a_consumption_meter_is_discarded(db):
    """Installed production belongs on the feed-in side.

    The field is editable on both directions, so the sum has to be the one
    that refuses it rather than trusting the form.
    """
    site_id = _site(db)
    _meter(db, site_id, direction=DIRECTION_FEED_IN, pv=9.0, suffix="01")
    _meter(db, site_id, direction=DIRECTION_CONSUMPTION, pv=99.0, suffix="02")

    totals = installed_capacity_totals(db)
    assert totals.pv_kwp == 9.0
    assert totals.pv_counted == 1
    assert totals.pv_expected == 1
    assert len(totals.implausible) == 1


def test_a_battery_counts_on_either_direction(db):
    """Storage sits behind the connection, not behind one direction."""
    site_id = _site(db)
    _meter(db, site_id, direction=DIRECTION_FEED_IN, battery=10.0, suffix="01")
    _meter(db, site_id, direction=DIRECTION_CONSUMPTION, battery=12.5, suffix="02")

    totals = installed_capacity_totals(db)
    assert totals.battery_kwh == 22.5
    assert totals.battery_counted == 2
    assert totals.implausible == []


def test_restricting_to_one_leg_sums_only_that_leg(db):
    """And the per-LEG sums add up to the overall sum."""
    create_demo_data(db)
    legs = leg_repo.list_all(db)

    overall = installed_capacity_totals(db)
    per_leg = [installed_capacity_totals(db, leg.id) for leg in legs]

    assert round(sum(t.pv_kwp for t in per_leg), 2) == overall.pv_kwp
    assert round(sum(t.battery_kwh for t in per_leg), 2) == overall.battery_kwh
    assert sum(t.pv_counted for t in per_leg) == overall.pv_counted


def test_no_metering_points_yields_zero_rather_than_an_error(db):
    """An empty deployment must render the overview, not crash it."""
    totals = installed_capacity_totals(db)

    assert totals.pv_kwp == 0.0
    assert totals.pv_counted == 0
    assert totals.pv_expected == 0
    assert totals.battery_kwh == 0.0
    assert totals.implausible == []
