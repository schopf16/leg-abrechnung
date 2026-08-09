"""Tests for the Statistik page's trend aggregations."""

from datetime import date

from app.domain.period import trailing_months
from app.domain.statistics import monthly_energy_totals, monthly_growth_counts
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import standort as standort_repo
from app.models.leg import Leg
from app.models.messpunkt import MESSRICHTUNG_BEZUG, MESSRICHTUNG_EINSPEISUNG, Messpunkt
from app.models.reading import Reading, upsert_readings
from app.models.standort import Standort


def test_trailing_months_lists_chronological_window_ending_at_reference():
    """`trailing_months` returns count months, oldest first, ending at the reference month."""
    months = trailing_months(date(2025, 3, 15), count=4)
    assert months == [(2024, 12), (2025, 1), (2025, 2), (2025, 3)]


def test_trailing_months_handles_year_boundary_for_single_month():
    """A 12-month window ending in January starts the previous February."""
    months = trailing_months(date(2025, 1, 1), count=12)
    assert months[0] == (2024, 2)
    assert months[-1] == (2025, 1)


def _make_standort(db) -> int:
    return standort_repo.create(
        db,
        Standort(
            id=None, adresse="Musterstrasse", hausnummer="1", plz="3000", gemeinde="Bern", lage="",
            trafokreis_id=None, created_at="",
        ),
    )


def _make_messpunkt(db, bezeichnung: str, messrichtung: str, standort_id: int, leg_id=None) -> int:
    return messpunkt_repo.create(
        db,
        Messpunkt(
            id=None, messpunkt_bezeichnung=bezeichnung, messrichtung=messrichtung,
            standort_id=standort_id, leg_id=leg_id, pv_leistung_kwp=None,
            batteriespeicher_kwh=None, created_at="",
        ),
    )


def _set_created_at(db, table: str, entity_id: int, when: date) -> None:
    """Overwrite a row's `created_at` for growth-bucketing tests."""
    db.execute(f"UPDATE {table} SET created_at = ? WHERE id = ?", (when.isoformat(), entity_id))
    db.commit()


def test_monthly_energy_totals_aggregates_by_month_and_direction(db):
    """Bezug and Einspeisung readings are summed per calendar month."""
    standort_id = _make_standort(db)
    bezug_mp = _make_messpunkt(db, "CH-B1", MESSRICHTUNG_BEZUG, standort_id)
    einspeisung_mp = _make_messpunkt(db, "CH-E1", MESSRICHTUNG_EINSPEISUNG, standort_id)

    upsert_readings(
        db,
        [
            Reading(messpunkt_id=bezug_mp, timestamp="2025-06-01T00:00:00", direction="bezug", kwh=10.0, source="test"),
            Reading(messpunkt_id=bezug_mp, timestamp="2025-06-01T00:15:00", direction="bezug", kwh=5.0, source="test"),
            Reading(messpunkt_id=einspeisung_mp, timestamp="2025-06-01T00:00:00", direction="einspeisung", kwh=3.0, source="test"),
            Reading(messpunkt_id=bezug_mp, timestamp="2025-05-01T00:00:00", direction="bezug", kwh=2.0, source="test"),
        ],
    )

    monthly = monthly_energy_totals(db, reference_date=date(2025, 6, 15), months=3)
    by_month = {(m.year, m.month): m for m in monthly}

    assert by_month[(2025, 6)].bezug_kwh == 15.0
    assert by_month[(2025, 6)].einspeisung_kwh == 3.0
    assert by_month[(2025, 6)].saldo_kwh == -12.0
    assert by_month[(2025, 5)].bezug_kwh == 2.0
    assert by_month[(2025, 4)].bezug_kwh == 0.0
    assert by_month[(2025, 4)].einspeisung_kwh == 0.0


def test_monthly_energy_totals_filters_by_leg(db):
    """Passing a leg_id only counts readings from that LEG's Messpunkte."""
    standort_id = _make_standort(db)
    leg_a = leg_repo.create(db, Leg(id=None, name="LEG A", bemerkung="", created_at=""))
    leg_b = leg_repo.create(db, Leg(id=None, name="LEG B", bemerkung="", created_at=""))
    mp_a = _make_messpunkt(db, "CH-A", MESSRICHTUNG_BEZUG, standort_id, leg_id=leg_a)
    mp_b = _make_messpunkt(db, "CH-B", MESSRICHTUNG_BEZUG, standort_id, leg_id=leg_b)

    upsert_readings(
        db,
        [
            Reading(messpunkt_id=mp_a, timestamp="2025-06-01T00:00:00", direction="bezug", kwh=7.0, source="test"),
            Reading(messpunkt_id=mp_b, timestamp="2025-06-01T00:00:00", direction="bezug", kwh=4.0, source="test"),
        ],
    )

    only_a = monthly_energy_totals(db, leg_id=leg_a, reference_date=date(2025, 6, 15), months=1)
    assert only_a[0].bezug_kwh == 7.0

    everything = monthly_energy_totals(db, leg_id=None, reference_date=date(2025, 6, 15), months=1)
    assert everything[0].bezug_kwh == 11.0


def test_monthly_growth_counts_are_cumulative(db):
    """A Standort created in an earlier month counts toward every later month too."""
    early_id = _make_standort(db)
    _set_created_at(db, "standort", early_id, date(2025, 1, 10))
    late_id = _make_standort(db)
    _set_created_at(db, "standort", late_id, date(2025, 3, 5))

    growth = monthly_growth_counts(db, reference_date=date(2025, 4, 30), months=4)
    by_month = {(g.year, g.month): g for g in growth}

    assert by_month[(2025, 1)].standorte == 1
    assert by_month[(2025, 2)].standorte == 1
    assert by_month[(2025, 3)].standorte == 2
    assert by_month[(2025, 4)].standorte == 2
