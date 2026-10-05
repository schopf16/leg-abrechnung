"""substation area (transformer circuit): the physical grid-topology grouping a site belongs to. Purely
a property of the site -- never of a Person or MeteringPoint directly."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class SubstationArea:
    """One physical transformer circuit."""

    id: Optional[int]
    name: str
    bkw_designation: str
    note: str
    created_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "SubstationArea":
        """Build a `substation area` from a `sqlite3.Row`."""
        return SubstationArea(
            id=row["id"],
            name=row["name"],
            bkw_designation=row["bkw_designation"],
            note=row["note"],
            created_at=row["created_at"],
        )


def list_all(connection: sqlite3.Connection) -> list[SubstationArea]:
    """List all substation areas, ordered by name."""
    rows = connection.execute("SELECT * FROM substation_area ORDER BY name").fetchall()
    return [SubstationArea.from_row(row) for row in rows]


def get(connection: sqlite3.Connection, substation_area_id: int) -> Optional[SubstationArea]:
    """Fetch a single substation area by id."""
    row = connection.execute("SELECT * FROM substation_area WHERE id = ?", (substation_area_id,)).fetchone()
    return SubstationArea.from_row(row) if row else None


def get_by_name(connection: sqlite3.Connection, name: str) -> Optional[SubstationArea]:
    """Fetch a single substation area by its exact name."""
    row = connection.execute("SELECT * FROM substation_area WHERE name = ?", (name,)).fetchone()
    return SubstationArea.from_row(row) if row else None


def create(connection: sqlite3.Connection, substation_area: SubstationArea) -> int:
    """Insert a new substation area."""
    cursor = connection.execute(
        """
        INSERT INTO substation_area (name, bkw_designation, note, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            substation_area.name,
            substation_area.bkw_designation,
            substation_area.note,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, substation_area: SubstationArea) -> None:
    """Update an existing substation area's data."""
    if substation_area.id is None:
        raise ValueError("Cannot update a substation area without an id.")
    connection.execute(
        """
        UPDATE substation_area SET
            name = ?, bkw_designation = ?, note = ?
        WHERE id = ?
        """,
        (substation_area.name, substation_area.bkw_designation, substation_area.note, substation_area.id),
    )
    connection.commit()


def count_sites(connection: sqlite3.Connection, substation_area_id: int) -> int:
    """Count the sites currently assigned to a substation area."""
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM site WHERE substation_area_id = ?", (substation_area_id,)
    ).fetchone()
    return row["n"]


class SubstationAreaInUseError(Exception):
    """Raised when deleting a substation area that still has assigned sites."""


def delete(connection: sqlite3.Connection, substation_area_id: int) -> None:
    """Delete a substation area, but only if no site is assigned to it."""
    if count_sites(connection, substation_area_id) > 0:
        raise SubstationAreaInUseError(
            "Trafokreis kann nicht gelöscht werden: es sind noch Standorte zugeordnet."
        )
    connection.execute("DELETE FROM substation_area WHERE id = ?", (substation_area_id,))
    connection.commit()
