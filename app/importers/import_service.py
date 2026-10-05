"""Orchestrates importing a reading file: parse, match metering points, store."""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app.importers.base import ImportValidationError, ParsedReading
from app.importers.csv_parser import parse_csv_file
from app.importers.ebix_parser import parse_ebix_file
from app.models import metering_point as metering_point_repo
from app.models.reading import ImportBatch, Reading, create_import_batch, now_iso, upsert_readings

#: File extensions dispatched to each parser.
_EBIX_EXTENSIONS = {".xml"}
_CSV_EXTENSIONS = {".csv"}


@dataclass
class ImportOutcome:
    """Result of importing one file, for display in the import UI."""

    filename: str
    format: str
    rows_stored: int = 0
    unknown_metering_point_designations: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    period_from: str | None = None
    period_to: str | None = None


def import_file(connection: sqlite3.Connection, path: Path) -> ImportOutcome:
    """Import one EBIX (`.xml`) or CSV (`.csv`) reading file."""
    suffix = path.suffix.lower()
    if suffix in _EBIX_EXTENSIONS:
        file_format = "ebix"
        parse_result = parse_ebix_file(path)
    elif suffix in _CSV_EXTENSIONS:
        file_format = "csv"
        parse_result = parse_csv_file(path)
    else:
        raise ImportValidationError(f"Nicht unterstützter Dateityp {suffix!r}. Erlaubt: .xml (EBIX), .csv.")

    outcome = ImportOutcome(filename=path.name, format=file_format, warnings=list(parse_result.warnings))

    if not parse_result.readings:
        return outcome

    metering_point_id_by_designation = _load_metering_point_lookup(connection)
    readings_to_store: list[Reading] = []
    for parsed in parse_result.readings:
        metering_point_id = metering_point_id_by_designation.get(parsed.designation)
        if metering_point_id is None:
            outcome.unknown_metering_point_designations.add(parsed.designation)
            continue
        readings_to_store.append(_to_reading(parsed, metering_point_id, file_format))

    timestamps = sorted(r.timestamp for r in parse_result.readings)
    outcome.period_from = timestamps[0].isoformat()
    outcome.period_to = timestamps[-1].isoformat()

    batch_id = create_import_batch(
        connection,
        ImportBatch(
            id=None,
            filename=path.name,
            format=file_format,
            imported_at=now_iso(),
            period_from=outcome.period_from,
            period_to=outcome.period_to,
            row_count=len(readings_to_store),
        ),
    )
    for reading in readings_to_store:
        reading.import_batch_id = batch_id

    outcome.rows_stored = upsert_readings(connection, readings_to_store)

    if outcome.unknown_metering_point_designations:
        outcome.warnings.append(
            "Unbekannte Messpunkt-Bezeichnungen (nicht importiert, zuerst "
            "als Messpunkt anlegen): " + ", ".join(sorted(outcome.unknown_metering_point_designations))
        )

    return outcome


def _load_metering_point_lookup(connection: sqlite3.Connection) -> dict[str, int]:
    """Build a designation-to-metering_point-id lookup for the whole registry."""
    return {mp.designation: mp.id for mp in metering_point_repo.list_all(connection)}


def _to_reading(parsed: ParsedReading, metering_point_id: int, file_format: str) -> Reading:
    """Convert a `ParsedReading` into a persistence-layer `Reading`."""
    return Reading(
        metering_point_id=metering_point_id,
        timestamp=parsed.timestamp.isoformat(),
        direction=parsed.direction,
        kwh=parsed.kwh,
        source=file_format,
        import_batch_id=None,
    )
