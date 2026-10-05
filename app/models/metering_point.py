"""MeteringPoint (metering point): the grid operator's own measurement point, uniquely identified by
its `designation` (VNB id) -- never by a postal address, which is not an identity key (an address
can host several metering points, e.g. a multi-family building)."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

#: A MeteringPoint measures either consumption or feed-in, never both.
DIRECTION_CONSUMPTION = "consumption"
DIRECTION_FEED_IN = "feed_in"
ALL_DIRECTIONS = frozenset({DIRECTION_CONSUMPTION, DIRECTION_FEED_IN})


@dataclass
class MeteringPoint:
    """A single grid operator metering point."""

    id: Optional[int]
    designation: str
    direction: str
    site_id: int
    leg_id: Optional[int]
    pv_capacity_kwp: Optional[float]
    battery_capacity_kwh: Optional[float]
    created_at: str
    label: str = ""
    # Added later, so it carries a default -- `label` set that precedent in
    # this very dataclass. Only the Beitrittserklärung asks for it.
    wallbox_capacity_kw: Optional[float] = None

    @property
    def is_consumption(self) -> bool:
        """Whether this MeteringPoint measures consumption."""
        return self.direction == DIRECTION_CONSUMPTION

    @property
    def is_feed_in(self) -> bool:
        """Whether this MeteringPoint measures feed-in."""
        return self.direction == DIRECTION_FEED_IN

    @staticmethod
    def from_row(row: sqlite3.Row) -> "MeteringPoint":
        """Build a `MeteringPoint` from a `sqlite3.Row`."""
        return MeteringPoint(
            id=row["id"],
            designation=row["designation"],
            direction=row["direction"],
            site_id=row["site_id"],
            leg_id=row["leg_id"],
            pv_capacity_kwp=row["pv_capacity_kwp"],
            battery_capacity_kwh=row["battery_capacity_kwh"],
            wallbox_capacity_kw=row["wallbox_capacity_kw"],
            created_at=row["created_at"],
            label=row["label"],
        )


def list_all(connection: sqlite3.Connection) -> list[MeteringPoint]:
    """List all metering points, ordered by their designation."""
    rows = connection.execute("SELECT * FROM metering_point ORDER BY designation").fetchall()
    return [MeteringPoint.from_row(row) for row in rows]


def list_for_site(connection: sqlite3.Connection, site_id: int) -> list[MeteringPoint]:
    """List all metering points belonging to one site."""
    rows = connection.execute(
        "SELECT * FROM metering_point WHERE site_id = ? ORDER BY designation",
        (site_id,),
    ).fetchall()
    return [MeteringPoint.from_row(row) for row in rows]


def get(connection: sqlite3.Connection, metering_point_id: int) -> Optional[MeteringPoint]:
    """Fetch a single MeteringPoint by id."""
    row = connection.execute("SELECT * FROM metering_point WHERE id = ?", (metering_point_id,)).fetchone()
    return MeteringPoint.from_row(row) if row else None


def get_by_designation(connection: sqlite3.Connection, designation: str) -> Optional[MeteringPoint]:
    """Fetch a single MeteringPoint by its business key."""
    row = connection.execute("SELECT * FROM metering_point WHERE designation = ?", (designation,)).fetchone()
    return MeteringPoint.from_row(row) if row else None


def create(connection: sqlite3.Connection, metering_point: MeteringPoint) -> int:
    """Insert a new MeteringPoint."""
    if metering_point.direction not in ALL_DIRECTIONS:
        raise ValueError(f"Unknown direction: {metering_point.direction!r}")
    cursor = connection.execute(
        """
        INSERT INTO metering_point
            (designation, direction, site_id, leg_id,
             pv_capacity_kwp, battery_capacity_kwh, created_at, label,
             wallbox_capacity_kw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metering_point.designation,
            metering_point.direction,
            metering_point.site_id,
            metering_point.leg_id,
            metering_point.pv_capacity_kwp,
            metering_point.battery_capacity_kwh,
            datetime.now(timezone.utc).isoformat(),
            metering_point.label.strip(),
            metering_point.wallbox_capacity_kw,
        ),
    )
    connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, metering_point: MeteringPoint) -> None:
    """Update an existing MeteringPoint's data."""
    if metering_point.id is None:
        raise ValueError("Cannot update a Messpunkt without an id.")
    if metering_point.direction not in ALL_DIRECTIONS:
        raise ValueError(f"Unknown direction: {metering_point.direction!r}")
    connection.execute(
        """
        UPDATE metering_point SET
            designation = ?, direction = ?, site_id = ?, leg_id = ?,
            pv_capacity_kwp = ?, battery_capacity_kwh = ?, label = ?,
            wallbox_capacity_kw = ?
        WHERE id = ?
        """,
        (
            metering_point.designation,
            metering_point.direction,
            metering_point.site_id,
            metering_point.leg_id,
            metering_point.pv_capacity_kwp,
            metering_point.battery_capacity_kwh,
            metering_point.label.strip(),
            metering_point.wallbox_capacity_kw,
            metering_point.id,
        ),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, metering_point_id: int) -> None:
    """Delete a MeteringPoint along with its assignments and readings (cascade)."""
    connection.execute("DELETE FROM metering_point WHERE id = ?", (metering_point_id,))
    connection.commit()
