"""15-minute MeteringPoint readings and the import batches that brought them in."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Reading:
    """A single 15-minute interval value for one MeteringPoint."""

    metering_point_id: int
    timestamp: str
    direction: str
    kwh: float
    source: str
    import_batch_id: Optional[int] = None


@dataclass
class ImportBatch:
    """Metadata about one completed import run."""

    id: Optional[int]
    filename: str
    format: str
    imported_at: str
    period_from: Optional[str]
    period_to: Optional[str]
    row_count: int


def create_import_batch(connection: sqlite3.Connection, batch: ImportBatch) -> int:
    """Insert a new import batch record."""
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
    """Insert readings, idempotently skipping ones that already exist."""
    connection.executemany(
        """
        INSERT INTO readings (metering_point_id, timestamp, direction, kwh, source, import_batch_id)
        VALUES (:metering_point_id, :timestamp, :direction, :kwh, :source, :import_batch_id)
        ON CONFLICT (metering_point_id, timestamp, direction) DO UPDATE SET
            kwh = excluded.kwh,
            source = excluded.source,
            import_batch_id = excluded.import_batch_id
        """,
        [
            {
                "metering_point_id": r.metering_point_id,
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
    """Fetch all readings for the given half-open time range, across metering points."""
    return connection.execute(
        """
        SELECT r.metering_point_id, r.timestamp, r.direction, r.kwh, mp.direction
        FROM readings r
        JOIN metering_point mp ON mp.id = r.metering_point_id
        WHERE r.timestamp >= ? AND r.timestamp < ?
        ORDER BY r.timestamp
        """,
        (start, end_exclusive),
    ).fetchall()


def list_import_batches(connection: sqlite3.Connection) -> list[ImportBatch]:
    """List all import batches, most recent first."""
    rows = connection.execute("SELECT * FROM import_batches ORDER BY imported_at DESC").fetchall()
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
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
