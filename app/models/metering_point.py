"""MeteringPoint (metering point): the grid operator's own measurement point,
uniquely identified by its `designation` (VNB id) -- never by a
postal address, which is not an identity key (an address can host several
metering points, e.g. a multi-family building).

LEG membership is deliberately a property of the MeteringPoint, not of its
site (see `app.models.leg`): two metering points at the very same site
can belong to different LEGs, since it is the MeteringPoint's owner -- not the
building -- who decides which LEG to join.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

#: A MeteringPoint measures either consumption or feed-in, never both.
DIRECTION_CONSUMPTION = "bezug"
DIRECTION_FEED_IN = "einspeisung"
ALL_DIRECTIONS = frozenset({DIRECTION_CONSUMPTION, DIRECTION_FEED_IN})


@dataclass
class MeteringPoint:
    """A single grid operator metering point.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        designation: The grid operator's own metering point id
            (business key), as used in EBIX/SDAT-CH and CSV exports.
        direction: Either `DIRECTION_CONSUMPTION` (consumption) or
            `DIRECTION_FEED_IN` (feed-in).
        site_id: Foreign key to the `site` this MeteringPoint is
            physically installed at.
        leg_id: Foreign key to the assigned `Leg`, `None` until manually
            assigned.
        pv_capacity_kwp: Installed PV capacity in kWp, `None` if unknown
            or not applicable (purely informational, not used in
            billing/distribution).
        battery_capacity_kwh: Battery storage capacity in kWh, `None` if
            unknown or not applicable (purely informational, not used in
            billing/distribution).
        created_at: ISO-8601 creation timestamp.
    """

    id: Optional[int]
    designation: str
    direction: str
    site_id: int
    leg_id: Optional[int]
    pv_capacity_kwp: Optional[float]
    battery_capacity_kwh: Optional[float]
    created_at: str

    @property
    def is_consumption(self) -> bool:
        """Whether this MeteringPoint measures consumption.

        Returns:
            `True` if `direction` is `DIRECTION_CONSUMPTION`.
        """
        return self.direction == DIRECTION_CONSUMPTION

    @property
    def is_feed_in(self) -> bool:
        """Whether this MeteringPoint measures feed-in.

        Returns:
            `True` if `direction` is `DIRECTION_FEED_IN`.
        """
        return self.direction == DIRECTION_FEED_IN

    @staticmethod
    def from_row(row: sqlite3.Row) -> "MeteringPoint":
        """Build a `MeteringPoint` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `metering_point` table.

        Returns:
            The corresponding `MeteringPoint` dataclass instance.
        """
        return MeteringPoint(
            id=row["id"],
            designation=row["designation"],
            direction=row["direction"],
            site_id=row["site_id"],
            leg_id=row["leg_id"],
            pv_capacity_kwp=row["pv_capacity_kwp"],
            battery_capacity_kwh=row["battery_capacity_kwh"],
            created_at=row["created_at"],
        )


def list_all(connection: sqlite3.Connection) -> list[MeteringPoint]:
    """List all metering points, ordered by their designation.

    Args:
        connection: Open SQLite connection.

    Returns:
        All metering points, sorted by `designation`.
    """
    rows = connection.execute(
        "SELECT * FROM metering_point ORDER BY designation"
    ).fetchall()
    return [MeteringPoint.from_row(row) for row in rows]


def list_for_site(connection: sqlite3.Connection, site_id: int) -> list[MeteringPoint]:
    """List all metering points belonging to one site.

    Args:
        connection: Open SQLite connection.
        site_id: Primary key of the site.

    Returns:
        All metering points at that site, sorted by `designation`.
    """
    rows = connection.execute(
        "SELECT * FROM metering_point WHERE site_id = ? ORDER BY designation",
        (site_id,),
    ).fetchall()
    return [MeteringPoint.from_row(row) for row in rows]


def get(connection: sqlite3.Connection, metering_point_id: int) -> Optional[MeteringPoint]:
    """Fetch a single MeteringPoint by id.

    Args:
        connection: Open SQLite connection.
        metering_point_id: Primary key of the metering point.

    Returns:
        The matching `MeteringPoint`, or `None` if no such id exists.
    """
    row = connection.execute(
        "SELECT * FROM metering_point WHERE id = ?", (metering_point_id,)
    ).fetchone()
    return MeteringPoint.from_row(row) if row else None


def get_by_designation(
    connection: sqlite3.Connection, designation: str
) -> Optional[MeteringPoint]:
    """Fetch a single MeteringPoint by its business key.

    Args:
        connection: Open SQLite connection.
        designation: Metering point id as it appears in grid
            operator data exports.

    Returns:
        The matching `MeteringPoint`, or `None` if unknown.
    """
    row = connection.execute(
        "SELECT * FROM metering_point WHERE designation = ?", (designation,)
    ).fetchone()
    return MeteringPoint.from_row(row) if row else None


def create(connection: sqlite3.Connection, metering_point: MeteringPoint) -> int:
    """Insert a new MeteringPoint.

    Args:
        connection: Open SQLite connection.
        metering_point: Data to insert; `id` and `created_at` are ignored and
            generated by this function.

    Returns:
        The primary key of the newly created metering point.

    Raises:
        ValueError: If `metering_point.direction` is not recognized.
    """
    if metering_point.direction not in ALL_DIRECTIONS:
        raise ValueError(f"Unknown direction: {metering_point.direction!r}")
    cursor = connection.execute(
        """
        INSERT INTO metering_point
            (designation, direction, site_id, leg_id,
             pv_capacity_kwp, battery_capacity_kwh, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metering_point.designation,
            metering_point.direction,
            metering_point.site_id,
            metering_point.leg_id,
            metering_point.pv_capacity_kwp,
            metering_point.battery_capacity_kwh,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, metering_point: MeteringPoint) -> None:
    """Update an existing MeteringPoint's data.

    Args:
        connection: Open SQLite connection.
        metering_point: Metering point with `id` set to an existing record.

    Returns:
        None.

    Raises:
        ValueError: If `metering_point.id` is `None` or `direction` is unrecognized.
    """
    if metering_point.id is None:
        raise ValueError("Cannot update a Messpunkt without an id.")
    if metering_point.direction not in ALL_DIRECTIONS:
        raise ValueError(f"Unknown direction: {metering_point.direction!r}")
    connection.execute(
        """
        UPDATE metering_point SET
            designation = ?, direction = ?, site_id = ?, leg_id = ?,
            pv_capacity_kwp = ?, battery_capacity_kwh = ?
        WHERE id = ?
        """,
        (
            metering_point.designation,
            metering_point.direction,
            metering_point.site_id,
            metering_point.leg_id,
            metering_point.pv_capacity_kwp,
            metering_point.battery_capacity_kwh,
            metering_point.id,
        ),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, metering_point_id: int) -> None:
    """Delete a MeteringPoint along with its assignments and readings (cascade).

    Args:
        connection: Open SQLite connection.
        metering_point_id: Primary key of the metering point to delete.

    Returns:
        None.
    """
    connection.execute("DELETE FROM metering_point WHERE id = ?", (metering_point_id,))
    connection.commit()
