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
from app.models import participant as participant_repo
from app.models import settings as settings_repo


def test_create_demo_data_creates_five_participants_and_seven_meters(db):
    """The generator creates 4 showcase participants + 1 move fixture, and 7 meters."""
    summary = create_demo_data(db)
    assert len(summary.participant_ids) == 5
    assert len(summary.meter_ids) == 7
    assert summary.reading_count > 0


def test_create_demo_data_configures_valid_demo_qr_iban(db):
    """The generator fills in LEG settings so demo QR-invoices can be generated right away."""
    create_demo_data(db)
    settings = settings_repo.get_settings(db)
    assert settings.qr_iban
    assert settings.name
    assert settings.address_street


def test_create_demo_data_is_guarded_against_double_run(db):
    """Running the generator twice raises instead of duplicating data."""
    create_demo_data(db)
    assert demo_data_exists(db)
    with pytest.raises(DemoDataAlreadyExists):
        create_demo_data(db)


def test_winter_quarter_has_zero_production(db):
    """Every production reading in the winter fixture quarter is zero."""
    create_demo_data(db)
    start, end = quarter_bounds(*WINTER_QUARTER)
    rows = db.execute(
        """
        SELECT r.kwh FROM readings r
        JOIN meters m ON m.id = r.meter_id
        WHERE m.role = 'produktion' AND r.timestamp >= ? AND r.timestamp < ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    assert rows, "expected production readings to exist for the winter quarter"
    assert all(row["kwh"] == 0.0 for row in rows)


def test_summer_quarter_has_both_surplus_and_deficit_intervals(db):
    """The summer fixture has intervals with production above and below consumption."""
    create_demo_data(db)
    start, end = quarter_bounds(*SUMMER_QUARTER)
    rows = db.execute(
        """
        SELECT r.timestamp, r.direction, r.kwh, m.role
        FROM readings r JOIN meters m ON m.id = r.meter_id
        WHERE r.timestamp >= ? AND r.timestamp < ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()

    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        bucket = totals.setdefault(row["timestamp"], {"bezug": 0.0, "produktion": 0.0})
        bucket[row["direction"]] += row["kwh"]

    surplus_intervals = sum(1 for v in totals.values() if v["produktion"] > v["bezug"])
    deficit_intervals = sum(1 for v in totals.values() if v["produktion"] < v["bezug"])
    assert surplus_intervals > 0
    assert deficit_intervals > 0


def test_demo_move_splits_meter_between_two_participants(db):
    """The Bergstrasse-4 meter is assigned to Erika, then to David, never both."""
    create_demo_data(db)
    participants = {p.name: p for p in participant_repo.list_all(db)}
    erika = participants["Erika Vorgängerin (Demo, Umzug-Beispiel)"]
    david = participants["David Demo (Demo)"]

    rows = db.execute(
        """
        SELECT participant_id, valid_from, valid_to FROM meter_assignments
        WHERE meter_id = (SELECT id FROM meters WHERE label = 'Bergstrasse 4 - Bezug')
        ORDER BY valid_from
        """
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["participant_id"] == erika.id
    assert rows[1]["participant_id"] == david.id
    assert rows[1]["valid_to"] is None
