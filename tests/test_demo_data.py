"""Tests for the demo data generator."""

import pytest

from app.domain.demo_data import (
    SUMMER_QUARTER,
    WINTER_QUARTER,
    DemoDataAlreadyExists,
    create_demo_data,
    demo_data_exists,
)
from app.domain.period import quarter_bounds
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models import substation_area as substation_area_repo


def test_create_demo_data_creates_five_personen_and_seven_metering_points(db):
    """The generator creates 4 showcase Personen + 1 move fixture, and 7 metering points."""
    summary = create_demo_data(db)
    assert len(summary.person_ids) == 5
    assert len(summary.metering_point_ids) == 7
    assert summary.reading_count > 0


def test_create_demo_data_configures_valid_demo_qr_iban(db):
    """The generator fills in settings, a substation area and a LEG so demo
    QR-invoices can be generated right away."""
    create_demo_data(db)
    settings = settings_repo.get_settings(db)
    assert settings.qr_iban
    assert settings.address_street
    substation_areas = substation_area_repo.list_all(db)
    assert len(substation_areas) == 1
    assert substation_areas[0].name
    legs = leg_repo.list_all(db)
    assert len(legs) == 1
    assert legs[0].name


def test_create_demo_data_assigns_leg_to_every_metering_point(db):
    """Every demo MeteringPoint has a LEG assigned (the demo LEG matches the demo substation area 1:1)."""
    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]
    for metering_point in metering_point_repo.list_all(db):
        assert metering_point.leg_id == leg.id


def test_create_demo_data_is_guarded_against_double_run(db):
    """Running the generator twice raises instead of duplicating data."""
    create_demo_data(db)
    assert demo_data_exists(db)
    with pytest.raises(DemoDataAlreadyExists):
        create_demo_data(db)


def test_winter_quarter_has_zero_production(db):
    """Every Einspeisung reading in the winter fixture quarter is zero."""
    create_demo_data(db)
    start, end = quarter_bounds(*WINTER_QUARTER)
    rows = db.execute(
        """
        SELECT r.kwh FROM readings r
        JOIN metering_point mp ON mp.id = r.metering_point_id
        WHERE mp.direction = 'einspeisung' AND r.timestamp >= ? AND r.timestamp < ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    assert rows, "expected Einspeisung readings to exist for the winter quarter"
    assert all(row["kwh"] == 0.0 for row in rows)


def test_summer_quarter_has_both_surplus_and_deficit_intervals(db):
    """The summer fixture has intervals with production above and below consumption."""
    create_demo_data(db)
    start, end = quarter_bounds(*SUMMER_QUARTER)
    rows = db.execute(
        """
        SELECT r.timestamp, r.direction, r.kwh, mp.direction
        FROM readings r JOIN metering_point mp ON mp.id = r.metering_point_id
        WHERE r.timestamp >= ? AND r.timestamp < ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()

    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        bucket = totals.setdefault(row["timestamp"], {"bezug": 0.0, "einspeisung": 0.0})
        bucket[row["direction"]] += row["kwh"]

    surplus_intervals = sum(1 for v in totals.values() if v["einspeisung"] > v["bezug"])
    deficit_intervals = sum(1 for v in totals.values() if v["einspeisung"] < v["bezug"])
    assert surplus_intervals > 0
    assert deficit_intervals > 0


def test_demo_move_splits_metering_point_between_two_personen(db):
    """The Bergstrasse-4 MeteringPoint is assigned to Erika, then to David, never both."""
    create_demo_data(db)
    personen = {p.anzeige_name: p for p in person_repo.list_all(db)}
    erika = personen["Erika Vorgängerin (Demo, Umzug-Beispiel)"]
    david = personen["David Demo (Demo)"]

    rows = db.execute(
        """
        SELECT person_id, valid_from, valid_to FROM assignment
        WHERE metering_point_id = (
            SELECT id FROM metering_point WHERE designation = 'CH1000000000000000000000007'
        )
        ORDER BY valid_from
        """
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["person_id"] == erika.id
    assert rows[1]["person_id"] == david.id
    assert rows[1]["valid_to"] is None
