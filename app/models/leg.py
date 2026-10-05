"""LEG (Lokale Elektrizitätsgemeinschaft): the administrative and billing group an individual
MeteringPoint opts into."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Leg:
    """One LEG (billing entity), attached to individual metering points."""

    id: Optional[int]
    name: str
    note: str
    created_at: str
    production_capacity_percent: Optional[float] = None
    production_capacity_recorded_at: Optional[str] = None

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Leg":
        """Build a `Leg` from a `sqlite3.Row`."""
        return Leg(
            id=row["id"],
            name=row["name"],
            note=row["note"],
            created_at=row["created_at"],
            production_capacity_percent=row["production_capacity_percent"],
            production_capacity_recorded_at=row["production_capacity_recorded_at"],
        )


def list_all(connection: sqlite3.Connection) -> list[Leg]:
    """List all LEGs, ordered by name."""
    rows = connection.execute("SELECT * FROM leg ORDER BY name").fetchall()
    return [Leg.from_row(row) for row in rows]


def get(connection: sqlite3.Connection, leg_id: int) -> Optional[Leg]:
    """Fetch a single LEG by id."""
    row = connection.execute("SELECT * FROM leg WHERE id = ?", (leg_id,)).fetchone()
    return Leg.from_row(row) if row else None


def get_by_name(connection: sqlite3.Connection, name: str) -> Optional[Leg]:
    """Fetch a single LEG by its exact name."""
    row = connection.execute("SELECT * FROM leg WHERE name = ?", (name,)).fetchone()
    return Leg.from_row(row) if row else None


def create(connection: sqlite3.Connection, leg: Leg) -> int:
    """Insert a new LEG."""
    cursor = connection.execute(
        """
        INSERT INTO leg
            (name, note, created_at, production_capacity_percent, production_capacity_recorded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            leg.name,
            leg.note,
            datetime.now(timezone.utc).isoformat(),
            leg.production_capacity_percent,
            leg.production_capacity_recorded_at,
        ),
    )
    connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, leg: Leg) -> None:
    """Update an existing LEG's data."""
    if leg.id is None:
        raise ValueError("Cannot update a LEG without an id.")
    connection.execute(
        """
        UPDATE leg SET
            name = ?, note = ?,
            production_capacity_percent = ?, production_capacity_recorded_at = ?
        WHERE id = ?
        """,
        (
            leg.name,
            leg.note,
            leg.production_capacity_percent,
            leg.production_capacity_recorded_at,
            leg.id,
        ),
    )
    connection.commit()


def count_metering_points(connection: sqlite3.Connection, leg_id: int) -> int:
    """Count the metering points currently assigned to a LEG."""
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM metering_point WHERE leg_id = ?", (leg_id,)
    ).fetchone()
    return row["n"]


class LegInUseError(Exception):
    """Raised when deleting a LEG that still has assigned metering points."""


def delete(connection: sqlite3.Connection, leg_id: int) -> None:
    """Delete a LEG, but only if no MeteringPoint is assigned to it."""
    if count_metering_points(connection, leg_id) > 0:
        raise LegInUseError("LEG kann nicht gelöscht werden: es sind noch Messpunkte zugeordnet.")
    connection.execute("DELETE FROM leg WHERE id = ?", (leg_id,))
    connection.commit()
