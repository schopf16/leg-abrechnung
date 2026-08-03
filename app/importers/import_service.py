"""Orchestrates importing a reading file: parse, match meters, store.

This is the only module the GUI talks to for imports. It dispatches to the
EBIX or CSV parser based on file extension, resolves each parsed reading's
metering point id against the local meter registry (reporting unknown ids
clearly instead of silently dropping them), and stores everything through
the idempotent `upsert_readings` repository function.
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app.importers.base import ImportValidationError, ParsedReading
from app.importers.csv_parser import parse_csv_file
from app.importers.ebix_parser import parse_ebix_file
from app.models import meter as meter_repo
from app.models.reading import ImportBatch, Reading, create_import_batch, now_iso, upsert_readings

#: File extensions dispatched to each parser.
_EBIX_EXTENSIONS = {".xml"}
_CSV_EXTENSIONS = {".csv"}


@dataclass
class ImportOutcome:
    """Result of importing one file, for display in the import UI.

    Attributes:
        filename: Name of the imported file.
        format: "ebix" or "csv".
        rows_stored: Number of readings inserted or updated.
        unknown_metering_point_ids: Metering point ids present in the file
            but not configured as a meter in the app.
        warnings: Parser-level warnings (skipped rows, etc.).
        period_from: Earliest interval timestamp seen (ISO string), if any.
        period_to: Latest interval timestamp seen (ISO string), if any.
    """

    filename: str
    format: str
    rows_stored: int = 0
    unknown_metering_point_ids: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    period_from: str | None = None
    period_to: str | None = None


def import_file(connection: sqlite3.Connection, path: Path) -> ImportOutcome:
    """Import one EBIX (`.xml`) or CSV (`.csv`) reading file.

    Idempotent: re-importing a file covering an already-imported period
    updates existing rows in place rather than duplicating them, relying
    on the `UNIQUE (meter_id, timestamp, direction)` database constraint.

    Args:
        connection: Open SQLite connection.
        path: Filesystem path of the file to import.

    Returns:
        An `ImportOutcome` summarizing what happened.

    Raises:
        ImportValidationError: If the file extension is unsupported, or
            the chosen parser rejects the file as structurally invalid.
    """
    suffix = path.suffix.lower()
    if suffix in _EBIX_EXTENSIONS:
        file_format = "ebix"
        parse_result = parse_ebix_file(path)
    elif suffix in _CSV_EXTENSIONS:
        file_format = "csv"
        parse_result = parse_csv_file(path)
    else:
        raise ImportValidationError(
            f"Nicht unterstützter Dateityp {suffix!r}. Erlaubt: .xml (EBIX), .csv."
        )

    outcome = ImportOutcome(
        filename=path.name, format=file_format, warnings=list(parse_result.warnings)
    )

    if not parse_result.readings:
        return outcome

    meter_id_by_metering_point = _load_meter_lookup(connection)
    readings_to_store: list[Reading] = []
    for parsed in parse_result.readings:
        meter_id = meter_id_by_metering_point.get(parsed.metering_point_id)
        if meter_id is None:
            outcome.unknown_metering_point_ids.add(parsed.metering_point_id)
            continue
        readings_to_store.append(_to_reading(parsed, meter_id, file_format))

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

    if outcome.unknown_metering_point_ids:
        outcome.warnings.append(
            "Unbekannte Zählpunkt-IDs (nicht importiert, zuerst als Zähler "
            "anlegen): " + ", ".join(sorted(outcome.unknown_metering_point_ids))
        )

    return outcome


def _load_meter_lookup(connection: sqlite3.Connection) -> dict[str, int]:
    """Build a metering-point-id-to-meter-id lookup for the whole registry.

    Args:
        connection: Open SQLite connection.

    Returns:
        A dict mapping `metering_point_id` to the meter's database id.
    """
    return {
        meter.metering_point_id: meter.id for meter in meter_repo.list_all(connection)
    }


def _to_reading(parsed: ParsedReading, meter_id: int, file_format: str) -> Reading:
    """Convert a `ParsedReading` into a persistence-layer `Reading`.

    Args:
        parsed: Reading parsed from the source file.
        meter_id: Resolved local meter id.
        file_format: "ebix" or "csv", stored as the reading's `source`.

    Returns:
        A `Reading` ready to be passed to `upsert_readings`.
    """
    return Reading(
        meter_id=meter_id,
        timestamp=parsed.timestamp.isoformat(),
        direction=parsed.direction,
        kwh=parsed.kwh,
        source=file_format,
        import_batch_id=None,
    )
