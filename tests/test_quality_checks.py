"""Tests for plausibility/consistency checks: Zuordnung gaps, reading
completeness and Messpunkte with no LEG assigned."""

import uuid
from datetime import date, datetime, timedelta

from app.domain.quality_checks import (
    check_assignment_consistency,
    check_leg_assignment,
    check_reading_completeness,
)
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import standort as standort_repo
from app.models import zuordnung as zuordnung_repo
from app.models.leg import Leg
from app.models.messpunkt import MESSRICHTUNG_BEZUG, Messpunkt
from app.models.person import Person
from app.models.reading import Reading, upsert_readings
from app.models.standort import Standort
from app.models.zuordnung import Zuordnung

YEAR, QUARTER = 2025, 1


def _person(db, name: str = "P") -> int:
    """Create a person and return its id."""
    return person_repo.create(
        db,
        Person(
            id=None, anrede="", firma="", vorname=name, nachname="",
            kontakt_email="", kontakt_telefon="",
            rechnungsadresse_strasse="", rechnungsadresse_plz="",
            rechnungsadresse_ort="", rechnungsadresse_land="CH",
            iban="", kundennummer=None, papierrechnung=False, created_at="",
        ),
    )


def _standort(db) -> int:
    """Create a Standort and return its id."""
    return standort_repo.create(
        db,
        Standort(
            id=None, adresse="Musterstrasse", hausnummer="1", plz="3000", gemeinde="Bern", lage="",
            trafokreis_id=None, created_at="",
        ),
    )


def _leg(db) -> int:
    """Create a LEG with a unique name and return its id."""
    name = f"Testkreis-{uuid.uuid4().hex[:8]}"
    return leg_repo.create(db, Leg(id=None, name=name, bemerkung="", created_at=""))


def _messpunkt(db, messpunkt_bezeichnung: str, standort_id: int, leg_id: int | None = None) -> int:
    """Create a "bezug" Messpunkt and return its id."""
    return messpunkt_repo.create(
        db,
        Messpunkt(
            id=None, messpunkt_bezeichnung=messpunkt_bezeichnung,
            messrichtung=MESSRICHTUNG_BEZUG, standort_id=standort_id, leg_id=leg_id,
            pv_leistung_kwp=None, batteriespeicher_kwh=None, created_at="",
        ),
    )


def test_check_assignment_consistency_reports_gaps_across_all_messpunkte(db):
    """A gap in one Messpunkt's assignment history is surfaced by the aggregate check."""
    person_id = _person(db)
    standort_id = _standort(db)
    messpunkt_id = _messpunkt(db, "CH-Q1", standort_id)
    zuordnung_repo.create(
        db,
        Zuordnung(
            id=None, person_id=person_id, messpunkt_id=messpunkt_id,
            gueltig_von=date(2025, 1, 1), gueltig_bis=date(2025, 1, 10), created_at="",
        ),
    )
    zuordnung_repo.create(
        db,
        Zuordnung(
            id=None, person_id=person_id, messpunkt_id=messpunkt_id,
            gueltig_von=date(2025, 1, 20), gueltig_bis=None, created_at="",
        ),
    )

    warnings = check_assignment_consistency(db)
    assert any(w.category == "zuordnung_luecke" for w in warnings)


def test_check_assignment_consistency_clean_history_has_no_warnings(db):
    """A single open-ended Zuordnung produces no warnings."""
    person_id = _person(db)
    standort_id = _standort(db)
    messpunkt_id = _messpunkt(db, "CH-Q1", standort_id)
    zuordnung_repo.create(
        db,
        Zuordnung(
            id=None, person_id=person_id, messpunkt_id=messpunkt_id,
            gueltig_von=date(2025, 1, 1), gueltig_bis=None, created_at="",
        ),
    )
    assert check_assignment_consistency(db) == []


def test_check_reading_completeness_flags_days_with_missing_values(db):
    """A day with fewer than 96 readings, while the Messpunkt is assigned, is flagged."""
    person_id = _person(db)
    standort_id = _standort(db)
    messpunkt_id = _messpunkt(db, "CH-Q1", standort_id)
    zuordnung_repo.create(
        db,
        Zuordnung(
            id=None, person_id=person_id, messpunkt_id=messpunkt_id,
            gueltig_von=date(YEAR, 1, 1), gueltig_bis=None, created_at="",
        ),
    )

    # Only 4 of the expected 96 readings for Jan 15th.
    day = datetime(YEAR, 1, 15)
    readings = [
        Reading(messpunkt_id=messpunkt_id, timestamp=(day + timedelta(minutes=15 * i)).isoformat(), direction="bezug", kwh=0.1, source="test")
        for i in range(4)
    ]
    upsert_readings(db, readings)

    warnings = check_reading_completeness(db, YEAR, QUARTER)
    assert any("2025-01-15" in w.message for w in warnings)


def test_check_reading_completeness_ignores_days_without_assignment(db):
    """A Messpunkt that was never assigned to anyone produces no completeness warnings."""
    standort_id = _standort(db)
    _messpunkt(db, "CH-Q1", standort_id)
    # No Zuordnung created at all.
    warnings = check_reading_completeness(db, YEAR, QUARTER)
    assert warnings == []


def test_check_reading_completeness_no_warning_for_fully_covered_day(db):
    """A day with exactly 96 readings is not flagged."""
    person_id = _person(db)
    standort_id = _standort(db)
    messpunkt_id = _messpunkt(db, "CH-Q1", standort_id)
    zuordnung_repo.create(
        db,
        Zuordnung(
            id=None, person_id=person_id, messpunkt_id=messpunkt_id,
            gueltig_von=date(YEAR, 1, 15), gueltig_bis=date(YEAR, 1, 15), created_at="",
        ),
    )
    day = datetime(YEAR, 1, 15)
    readings = [
        Reading(messpunkt_id=messpunkt_id, timestamp=(day + timedelta(minutes=15 * i)).isoformat(), direction="bezug", kwh=0.1, source="test")
        for i in range(96)
    ]
    upsert_readings(db, readings)

    warnings = check_reading_completeness(db, YEAR, QUARTER)
    assert not any("2025-01-15" in w.message for w in warnings)


def test_check_leg_assignment_no_warnings_when_all_assigned(db):
    """Messpunkte that all have a LEG assigned produce no warnings."""
    leg_a = _leg(db)
    leg_b = _leg(db)
    standort = _standort(db)
    _messpunkt(db, "CH-A", standort, leg_id=leg_a)
    _messpunkt(db, "CH-B", standort, leg_id=leg_b)

    # Multiple different LEGs in use at once is normal, not a warning.
    assert check_leg_assignment(db) == []


def test_check_leg_assignment_flags_unresolved_messpunkt(db):
    """A Messpunkt with no LEG assigned is flagged."""
    leg_id = _leg(db)
    standort = _standort(db)
    _messpunkt(db, "CH-A", standort, leg_id=leg_id)
    _messpunkt(db, "CH-C", standort, leg_id=None)

    warnings = check_leg_assignment(db)
    assert any(w.category == "leg_nicht_zugeordnet" for w in warnings)
    assert len(warnings) == 1


def test_check_leg_assignment_ignores_other_messpunkte_with_leg(db):
    """Messpunkte that already have a LEG don't influence the check."""
    leg_id = _leg(db)
    standort = _standort(db)
    _messpunkt(db, "CH-A", standort, leg_id=leg_id)

    assert check_leg_assignment(db) == []
