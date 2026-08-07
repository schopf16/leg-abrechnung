"""Tests for the EBIX/CSV import pipeline: parsing, matching, idempotency."""

from pathlib import Path

import pytest

from app.importers.base import ImportValidationError
from app.importers.csv_parser import parse_csv_file
from app.importers.ebix_parser import parse_ebix_file
from app.importers.import_service import import_file
from app.models import messpunkt as messpunkt_repo
from app.models import standort as standort_repo
from app.models.messpunkt import MESSRICHTUNG_BEZUG, MESSRICHTUNG_EINSPEISUNG, Messpunkt
from app.models.standort import Standort

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_standort(db) -> int:
    """Create a minimal Standort and return its id.

    Args:
        db: Database connection fixture.

    Returns:
        The new Standort's id.
    """
    return standort_repo.create(
        db,
        Standort(
            id=None, adresse="Musterstrasse", hausnummer="1", plz="3000", gemeinde="Bern", lage="",
            leg_id=None, netzebene="NE7", created_at="",
        ),
    )


def _make_messpunkt(messpunkt_bezeichnung: str, messrichtung: str, standort_id: int) -> Messpunkt:
    """Build an unpersisted `Messpunkt` for use in tests.

    Args:
        messpunkt_bezeichnung: Business key to assign.
        messrichtung: Measurement direction.
        standort_id: Foreign key of the site the Messpunkt belongs to.

    Returns:
        A `Messpunkt` with `id=None`.
    """
    return Messpunkt(
        id=None,
        messpunkt_bezeichnung=messpunkt_bezeichnung,
        messrichtung=messrichtung,
        standort_id=standort_id,
        created_at="",
    )


def test_parse_ebix_file_extracts_readings_in_position_order():
    """The EBIX parser turns position-indexed values into timestamped readings."""
    result = parse_ebix_file(FIXTURES_DIR / "sample_ebix.xml")
    assert not result.warnings
    assert len(result.readings) == 8

    consumption = [r for r in result.readings if r.messpunkt_bezeichnung == "CH1000000000000000000000001"]
    assert len(consumption) == 4
    assert all(r.direction == "bezug" for r in consumption)
    assert consumption[0].timestamp.isoformat() == "2025-07-01T00:00:00"
    assert consumption[1].timestamp.isoformat() == "2025-07-01T00:15:00"
    assert consumption[0].kwh == pytest.approx(0.1)

    production = [r for r in result.readings if r.messpunkt_bezeichnung == "CH1000000000000000000000002"]
    assert all(r.direction == "einspeisung" for r in production)


def test_parse_ebix_file_rejects_unsupported_resolution():
    """A resolution other than PT15M is a hard, explained error."""
    with pytest.raises(ImportValidationError, match="PT15M"):
        parse_ebix_file(FIXTURES_DIR / "sample_ebix_bad_resolution.xml")


def test_parse_csv_file_extracts_readings_with_comma_decimal():
    """The CSV parser accepts semicolon delimiters and comma decimals."""
    result = parse_csv_file(FIXTURES_DIR / "sample_readings.csv")
    assert not result.warnings
    assert len(result.readings) == 4
    first = result.readings[0]
    assert first.messpunkt_bezeichnung == "CH1000000000000000000000001"
    assert first.direction == "bezug"
    assert first.kwh == pytest.approx(0.1)


def test_import_file_stores_readings_for_known_messpunkte(db):
    """Importing a file with all Messpunkte registered stores every reading."""
    standort_id = _make_standort(db)
    messpunkt_repo.create(db, _make_messpunkt("CH1000000000000000000000001", MESSRICHTUNG_BEZUG, standort_id))
    messpunkt_repo.create(db, _make_messpunkt("CH1000000000000000000000002", MESSRICHTUNG_EINSPEISUNG, standort_id))

    outcome = import_file(db, FIXTURES_DIR / "sample_ebix.xml")

    assert outcome.rows_stored == 8
    assert outcome.unknown_messpunkt_bezeichnungen == set()
    stored = db.execute("SELECT COUNT(*) AS n FROM readings").fetchone()["n"]
    assert stored == 8


def test_import_file_reports_unknown_messpunkte(db):
    """Readings for Messpunkte without a matching registry entry are skipped and reported."""
    standort_id = _make_standort(db)
    messpunkt_repo.create(db, _make_messpunkt("CH1000000000000000000000001", MESSRICHTUNG_BEZUG, standort_id))
    # CH...0002 is intentionally not registered.

    outcome = import_file(db, FIXTURES_DIR / "sample_ebix.xml")

    assert outcome.rows_stored == 4
    assert outcome.unknown_messpunkt_bezeichnungen == {"CH1000000000000000000000002"}
    assert any("Unbekannte" in w for w in outcome.warnings)


def test_import_file_is_idempotent(db):
    """Importing the same file twice does not duplicate readings."""
    standort_id = _make_standort(db)
    messpunkt_repo.create(db, _make_messpunkt("CH1000000000000000000000001", MESSRICHTUNG_BEZUG, standort_id))
    messpunkt_repo.create(db, _make_messpunkt("CH1000000000000000000000002", MESSRICHTUNG_EINSPEISUNG, standort_id))

    import_file(db, FIXTURES_DIR / "sample_ebix.xml")
    import_file(db, FIXTURES_DIR / "sample_ebix.xml")

    stored = db.execute("SELECT COUNT(*) AS n FROM readings").fetchone()["n"]
    assert stored == 8


def test_import_file_csv_matches_ebix_readings(db):
    """The CSV fallback produces the same stored readings as the EBIX file."""
    standort_id = _make_standort(db)
    messpunkt_repo.create(db, _make_messpunkt("CH1000000000000000000000001", MESSRICHTUNG_BEZUG, standort_id))
    messpunkt_repo.create(db, _make_messpunkt("CH1000000000000000000000002", MESSRICHTUNG_EINSPEISUNG, standort_id))

    outcome = import_file(db, FIXTURES_DIR / "sample_readings.csv")

    assert outcome.format == "csv"
    assert outcome.rows_stored == 4


def test_import_file_rejects_unsupported_extension(tmp_path, db):
    """A file with an unrecognized extension is rejected with a clear error."""
    bad_file = tmp_path / "readings.txt"
    bad_file.write_text("irrelevant")
    with pytest.raises(ImportValidationError):
        import_file(db, bad_file)
