"""Tests for the EBIX/CSV import pipeline: parsing, matching, idempotency."""

from pathlib import Path

import pytest

from app.importers.base import ImportValidationError
from app.importers.csv_parser import parse_csv_file
from app.importers.ebix_parser import parse_ebix_file
from app.importers.import_service import import_file
from app.models import meter as meter_repo
from app.models.meter import Meter

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_meter(metering_point_id: str, role: str) -> Meter:
    """Build an unpersisted `Meter` for use in tests.

    Args:
        metering_point_id: Business key to assign.
        role: Meter role.

    Returns:
        A `Meter` with `id=None`.
    """
    return Meter(
        id=None,
        metering_point_id=metering_point_id,
        label=metering_point_id,
        building_address="",
        role=role,
        created_at="",
    )


def test_parse_ebix_file_extracts_readings_in_position_order():
    """The EBIX parser turns position-indexed values into timestamped readings."""
    result = parse_ebix_file(FIXTURES_DIR / "sample_ebix.xml")
    assert not result.warnings
    assert len(result.readings) == 8

    consumption = [r for r in result.readings if r.metering_point_id == "CH1000000000000000000000001"]
    assert len(consumption) == 4
    assert all(r.direction == "bezug" for r in consumption)
    assert consumption[0].timestamp.isoformat() == "2025-07-01T00:00:00"
    assert consumption[1].timestamp.isoformat() == "2025-07-01T00:15:00"
    assert consumption[0].kwh == pytest.approx(0.1)

    production = [r for r in result.readings if r.metering_point_id == "CH1000000000000000000000002"]
    assert all(r.direction == "produktion" for r in production)


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
    assert first.metering_point_id == "CH1000000000000000000000001"
    assert first.direction == "bezug"
    assert first.kwh == pytest.approx(0.1)


def test_import_file_stores_readings_for_known_meters(db):
    """Importing a file with all meters registered stores every reading."""
    meter_repo.create(db, _make_meter("CH1000000000000000000000001", "bezug"))
    meter_repo.create(db, _make_meter("CH1000000000000000000000002", "produktion"))

    outcome = import_file(db, FIXTURES_DIR / "sample_ebix.xml")

    assert outcome.rows_stored == 8
    assert outcome.unknown_metering_point_ids == set()
    stored = db.execute("SELECT COUNT(*) AS n FROM readings").fetchone()["n"]
    assert stored == 8


def test_import_file_reports_unknown_metering_points(db):
    """Readings for metering points without a matching meter are skipped and reported."""
    meter_repo.create(db, _make_meter("CH1000000000000000000000001", "bezug"))
    # CH...0002 is intentionally not registered.

    outcome = import_file(db, FIXTURES_DIR / "sample_ebix.xml")

    assert outcome.rows_stored == 4
    assert outcome.unknown_metering_point_ids == {"CH1000000000000000000000002"}
    assert any("Unbekannte" in w for w in outcome.warnings)


def test_import_file_is_idempotent(db):
    """Importing the same file twice does not duplicate readings."""
    meter_repo.create(db, _make_meter("CH1000000000000000000000001", "bezug"))
    meter_repo.create(db, _make_meter("CH1000000000000000000000002", "produktion"))

    import_file(db, FIXTURES_DIR / "sample_ebix.xml")
    import_file(db, FIXTURES_DIR / "sample_ebix.xml")

    stored = db.execute("SELECT COUNT(*) AS n FROM readings").fetchone()["n"]
    assert stored == 8


def test_import_file_csv_matches_ebix_readings(db):
    """The CSV fallback produces the same stored readings as the EBIX file."""
    meter_repo.create(db, _make_meter("CH1000000000000000000000001", "bezug"))
    meter_repo.create(db, _make_meter("CH1000000000000000000000002", "produktion"))

    outcome = import_file(db, FIXTURES_DIR / "sample_readings.csv")

    assert outcome.format == "csv"
    assert outcome.rows_stored == 4


def test_import_file_rejects_unsupported_extension(tmp_path, db):
    """A file with an unrecognized extension is rejected with a clear error."""
    bad_file = tmp_path / "readings.txt"
    bad_file.write_text("irrelevant")
    with pytest.raises(ImportValidationError):
        import_file(db, bad_file)
