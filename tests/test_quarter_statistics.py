"""Tests for the per-quarter data overview used to sanity-check an import."""

from datetime import date

import pytest

from app.domain.demo_data import SUMMER_QUARTER, WINTER_QUARTER, create_demo_data
from app.domain.statistics import quarter_energy_totals
from app.models import assignment as assignment_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models.assignment import Assignment
from app.models.metering_point import DIRECTION_CONSUMPTION, MeteringPoint
from app.models.site import Site


def _totals_for(db, year, quarter, leg_id=None):
    """Fetch one quarter's summary."""
    return next(
        (t for t in quarter_energy_totals(db, leg_id) if (t.year, t.quarter) == (year, quarter)),
        None,
    )


def test_totals_match_the_raw_readings(db):
    """Every figure must equal what a plain SQL sum over `readings` says."""
    create_demo_data(db)

    for total in quarter_energy_totals(db):
        rows = db.execute(
            """
            SELECT direction, SUM(kwh) AS kwh, COUNT(*) AS n
            FROM readings
            WHERE CAST(substr(timestamp, 1, 4) AS INTEGER) = ?
              AND (CAST(substr(timestamp, 6, 2) AS INTEGER) - 1) / 3 + 1 = ?
            GROUP BY direction
            """,
            (total.year, total.quarter),
        ).fetchall()
        raw = {row["direction"]: row for row in rows}
        assert total.consumption_kwh == pytest.approx(
            raw.get("consumption", {"kwh": 0.0})["kwh"] or 0.0, abs=0.001
        )
        assert total.feed_in_kwh == pytest.approx(raw.get("feed_in", {"kwh": 0.0})["kwh"] or 0.0, abs=0.001)
        assert total.reading_count == sum(row["n"] for row in rows)


def test_a_quarter_without_feed_in_is_flagged_as_unshareable(db):
    """The demo's winter quarter is exactly the trap that cost an evening."""
    create_demo_data(db)

    winter = _totals_for(db, *WINTER_QUARTER)
    summer = _totals_for(db, *SUMMER_QUARTER)

    assert winter.reading_count > 0, "das Quartal hat sehr wohl Messdaten"
    assert winter.feed_in_kwh == 0.0
    assert winter.can_share is False
    assert "Keine Einspeisung" in winter.note

    assert summer.can_share is True
    assert summer.note is None


def test_filtering_by_leg_adds_up_to_the_total(db):
    """Per-LEG figures must reconcile with the all-LEG figures."""
    create_demo_data(db)
    legs = leg_repo.list_all(db)

    overall = _totals_for(db, *SUMMER_QUARTER)
    per_leg = [_totals_for(db, *SUMMER_QUARTER, leg.id) for leg in legs]

    assert overall.consumption_kwh == pytest.approx(sum(t.consumption_kwh for t in per_leg if t), abs=0.001)
    assert overall.feed_in_kwh == pytest.approx(sum(t.feed_in_kwh for t in per_leg if t), abs=0.001)


def test_a_metering_point_without_readings_shows_up_as_a_shortfall(db):
    """An assigned meter that delivered nothing is the partial-import symptom."""
    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]
    before = _totals_for(db, *SUMMER_QUARTER, leg.id)
    assert before.missing_metering_points == 0, "Demodaten sind vollständig"

    # Add a metering point that is assigned but never reported a reading --
    # exactly what a half-finished import leaves behind.
    site_id = site_repo.create(
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
    metering_point_id = metering_point_repo.create(
        db,
        MeteringPoint(
            id=None,
            designation="CH1000000000000000000000099",
            direction=DIRECTION_CONSUMPTION,
            site_id=site_id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
    )
    person = person_repo.list_all(db)[0]
    assignment_repo.create(
        db,
        Assignment(
            id=None,
            person_id=person.id,
            metering_point_id=metering_point_id,
            valid_from=date(SUMMER_QUARTER[0], 1, 1),
            valid_to=None,
            created_at="",
        ),
    )

    after = _totals_for(db, *SUMMER_QUARTER, leg.id)
    assert after.metering_points_expected == before.metering_points_expected + 1
    assert after.metering_points_with_readings == before.metering_points_with_readings
    assert after.missing_metering_points == 1
    assert "ohne Messdaten" in after.note


def test_an_assignment_ending_before_the_quarter_is_not_expected(db):
    """A meter whose assignment ended earlier is not a missing import."""
    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]

    winter = _totals_for(db, *WINTER_QUARTER, leg.id)
    summer = _totals_for(db, *SUMMER_QUARTER, leg.id)

    # The demo's previous tenant hands over mid-August; her metering point
    # continues under the new tenant, so the count is unchanged, and no
    # phantom shortfall appears in either quarter.
    assert winter.missing_metering_points == 0
    assert summer.missing_metering_points == 0


def test_demo_data_reports_no_import_timestamp(db):
    """Readings that came from no import batch say so rather than inventing one."""
    create_demo_data(db)
    total = _totals_for(db, *SUMMER_QUARTER)

    assert total.last_import_at is None
    assert total.import_sources == ["demo"]
