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
from app.models import person as person_repo
from app.models import settings as settings_repo


def test_create_demo_data_creates_five_personen_and_seven_messpunkte(db):
    """The generator creates 4 showcase Personen + 1 move fixture, and 7 Messpunkte."""
    summary = create_demo_data(db)
    assert len(summary.person_ids) == 5
    assert len(summary.messpunkt_ids) == 7
    assert summary.reading_count > 0


def test_create_demo_data_configures_valid_demo_qr_iban(db):
    """The generator fills in settings and a LEG so demo QR-invoices can be
    generated right away."""
    create_demo_data(db)
    settings = settings_repo.get_settings(db)
    assert settings.qr_iban
    assert settings.address_street
    legs = leg_repo.list_all(db)
    assert len(legs) == 1
    assert legs[0].name


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
        JOIN messpunkt mp ON mp.id = r.messpunkt_id
        WHERE mp.messrichtung = 'einspeisung' AND r.timestamp >= ? AND r.timestamp < ?
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
        SELECT r.timestamp, r.direction, r.kwh, mp.messrichtung
        FROM readings r JOIN messpunkt mp ON mp.id = r.messpunkt_id
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


def test_demo_move_splits_messpunkt_between_two_personen(db):
    """The Bergstrasse-4 Messpunkt is assigned to Erika, then to David, never both."""
    create_demo_data(db)
    personen = {p.name: p for p in person_repo.list_all(db)}
    erika = personen["Erika Vorgängerin (Demo, Umzug-Beispiel)"]
    david = personen["David Demo (Demo)"]

    rows = db.execute(
        """
        SELECT person_id, gueltig_von, gueltig_bis FROM zuordnung
        WHERE messpunkt_id = (
            SELECT id FROM messpunkt WHERE messpunkt_bezeichnung = 'CH1000000000000000000000007'
        )
        ORDER BY gueltig_von
        """
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["person_id"] == erika.id
    assert rows[1]["person_id"] == david.id
    assert rows[1]["gueltig_bis"] is None
