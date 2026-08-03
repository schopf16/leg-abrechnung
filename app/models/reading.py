"""15-minute meter readings and the import batches that brought them in."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Reading:
    """A single 15-minute interval value for one meter.

    Attributes:
        meter_id: Foreign key to the meter this reading belongs to.
        timestamp: Interval start, as an ISO-8601 local datetime string
            (e.g. "2026-04-01T00:00:00").
        direction: Either "bezug" (consumption) or "produktion" (production)
            as delivered by the source file; independent from the meter's
            configured role so mismatches can be detected.
        kwh: Energy for this interval, in kWh, non-negative.
        source: Origin of the value, e.g. "ebix" or "csv".
        import_batch_id: Foreign key to the `import_batches` row that
            created this reading, if imported (vs. demo data).
    """

    meter_id: int
    timestamp: str
    direction: str
    kwh: float
    source: str
    import_batch_id: Optional[int] = None


@dataclass
class ImportBatch:
    """Metadata about one completed import run.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        filename: Name of the imported file, for traceability.
        format: Either "ebix" or "csv".
        imported_at: ISO-8601 timestamp of the import.
        period_from: Earliest interval timestamp seen in the file.
        period_to: Latest interval timestamp seen in the file.
        row_count: Number of readings inserted or updated by this batch.
    """

    id: Optional[int]
    filename: str
    format: str
    imported_at: str
    period_from: Optional[str]
    period_to: Optional[str]
    row_count: int


def create_import_batch(connection: sqlite3.Connection, batch: ImportBatch) -> int:
    """Insert a new import batch record.

    Args:
        connection: Open SQLite connection.
        batch: Batch metadata to insert; `id` is ignored and generated.

    Returns:
        The primary key of the newly created import batch.
    """
    cursor = connection.execute(
        """
        INSERT INTO import_batches
            (filename, format, imported_at, period_from, period_to, row_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            batch.filename,
            batch.format,
            batch.imported_at,
            batch.period_from,
            batch.period_to,
            batch.row_count,
        ),
    )
    connection.commit()
    return cursor.lastrowid


def upsert_readings(connection: sqlite3.Connection, readings: list[Reading]) -> int:
    """Insert readings, idempotently skipping ones that already exist.

    Idempotency relies on the `UNIQUE (meter_id, timestamp, direction)`
    constraint: re-importing the same period is safe and never creates
    duplicates. If a value for an existing (meter, timestamp, direction)
    changes between imports, the newer value overwrites the old one.

    Args:
        connection: Open SQLite connection.
        readings: Readings to insert or update.

    Returns:
        The number of readings inserted or updated.
    """
    connection.executemany(
        """
        INSERT INTO readings (meter_id, timestamp, direction, kwh, source, import_batch_id)
        VALUES (:meter_id, :timestamp, :direction, :kwh, :source, :import_batch_id)
        ON CONFLICT (meter_id, timestamp, direction) DO UPDATE SET
            kwh = excluded.kwh,
            source = excluded.source,
            import_batch_id = excluded.import_batch_id
        """,
        [
            {
                "meter_id": r.meter_id,
                "timestamp": r.timestamp,
                "direction": r.direction,
                "kwh": r.kwh,
                "source": r.source,
                "import_batch_id": r.import_batch_id,
            }
            for r in readings
        ],
    )
    connection.commit()
    return len(readings)


def list_readings_in_period(
    connection: sqlite3.Connection, start: str, end_exclusive: str
) -> list[sqlite3.Row]:
    """Fetch all readings for the given half-open time range, across meters.

    Args:
        connection: Open SQLite connection.
        start: ISO-8601 timestamp, inclusive lower bound.
        end_exclusive: ISO-8601 timestamp, exclusive upper bound.

    Returns:
        Rows with columns `meter_id`, `timestamp`, `direction`, `kwh`,
        joined with the meter's `role`, ordered by timestamp.
    """
    return connection.execute(
        """
        SELECT r.meter_id, r.timestamp, r.direction, r.kwh, m.role
        FROM readings r
        JOIN meters m ON m.id = r.meter_id
        WHERE r.timestamp >= ? AND r.timestamp < ?
        ORDER BY r.timestamp
        """,
        (start, end_exclusive),
    ).fetchall()


def list_import_batches(connection: sqlite3.Connection) -> list[ImportBatch]:
    """List all import batches, most recent first.

    Args:
        connection: Open SQLite connection.

    Returns:
        All import batches ordered by `imported_at` descending.
    """
    rows = connection.execute(
        "SELECT * FROM import_batches ORDER BY imported_at DESC"
    ).fetchall()
    return [
        ImportBatch(
            id=row["id"],
            filename=row["filename"],
            format=row["format"],
            imported_at=row["imported_at"],
            period_from=row["period_from"],
            period_to=row["period_to"],
            row_count=row["row_count"],
        )
        for row in rows
    ]


def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        Current time formatted with `datetime.isoformat`.
    """
    return datetime.now(timezone.utc).isoformat()
