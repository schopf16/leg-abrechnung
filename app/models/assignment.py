"""Time-bounded assignments of metering points to persons (assignment history)."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional


@dataclass
class Assignment:
    """One period during which a MeteringPoint was billed to a given Person."""

    id: Optional[int]
    person_id: int
    metering_point_id: int
    valid_from: date
    valid_to: Optional[date]
    created_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Assignment":
        """Build a `Assignment` from a `sqlite3.Row`."""
        return Assignment(
            id=row["id"],
            person_id=row["person_id"],
            metering_point_id=row["metering_point_id"],
            valid_from=date.fromisoformat(row["valid_from"]),
            valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
            created_at=row["created_at"],
        )

    def covers(self, moment: datetime) -> bool:
        """Check whether this Assignment is active at a given point in time."""
        moment_date = moment.date()
        if moment_date < self.valid_from:
            return False
        if self.valid_to is not None and moment_date > self.valid_to:
            return False
        return True

    def is_current_or_upcoming(self, moment: datetime) -> bool:
        """Whether this Assignment has not ended yet, as of `moment`."""
        return self.valid_to is None or self.valid_to >= moment.date()


def list_for_metering_point(connection: sqlite3.Connection, metering_point_id: int) -> list[Assignment]:
    """List all assignments of a MeteringPoint, most recent `valid_from` first."""
    rows = connection.execute(
        "SELECT * FROM assignment WHERE metering_point_id = ? ORDER BY valid_from DESC",
        (metering_point_id,),
    ).fetchall()
    return [Assignment.from_row(row) for row in rows]


def get_relevant_for_metering_point(
    connection: sqlite3.Connection, metering_point_id: int, moment: datetime
) -> Optional[Assignment]:
    """The Assignment to show as "currently assigned" for one MeteringPoint."""
    candidates = [
        z for z in list_for_metering_point(connection, metering_point_id) if z.is_current_or_upcoming(moment)
    ]
    for assignment in candidates:
        if assignment.covers(moment):
            return assignment
    if not candidates:
        return None
    return min(candidates, key=lambda z: z.valid_from)


def list_for_person(connection: sqlite3.Connection, person_id: int) -> list[Assignment]:
    """List all assignments of a Person, most recent `valid_from` first."""
    rows = connection.execute(
        "SELECT * FROM assignment WHERE person_id = ? ORDER BY valid_from DESC",
        (person_id,),
    ).fetchall()
    return [Assignment.from_row(row) for row in rows]


def get(connection: sqlite3.Connection, assignment_id: int) -> Optional[Assignment]:
    """Fetch a single Assignment by id."""
    row = connection.execute("SELECT * FROM assignment WHERE id = ?", (assignment_id,)).fetchone()
    return Assignment.from_row(row) if row else None


def list_all(connection: sqlite3.Connection) -> list[Assignment]:
    """List every Assignment in the database."""
    rows = connection.execute("SELECT * FROM assignment ORDER BY metering_point_id, valid_from").fetchall()
    return [Assignment.from_row(row) for row in rows]


def create(connection: sqlite3.Connection, assignment: Assignment) -> int:
    """Insert a new Assignment."""
    cursor = connection.execute(
        """
        INSERT INTO assignment (person_id, metering_point_id, valid_from, valid_to, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            assignment.person_id,
            assignment.metering_point_id,
            assignment.valid_from.isoformat(),
            assignment.valid_to.isoformat() if assignment.valid_to else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, assignment: Assignment) -> None:
    """Update an existing Assignment's validity period or person."""
    if assignment.id is None:
        raise ValueError("Cannot update a Zuordnung without an id.")
    connection.execute(
        """
        UPDATE assignment SET
            person_id = ?, metering_point_id = ?, valid_from = ?, valid_to = ?
        WHERE id = ?
        """,
        (
            assignment.person_id,
            assignment.metering_point_id,
            assignment.valid_from.isoformat(),
            assignment.valid_to.isoformat() if assignment.valid_to else None,
            assignment.id,
        ),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, assignment_id: int) -> None:
    """Delete a Assignment."""
    connection.execute("DELETE FROM assignment WHERE id = ?", (assignment_id,))
    connection.commit()


@dataclass
class AssignmentWarning:
    """A detected problem in a MeteringPoint's assignment history."""

    metering_point_id: int
    kind: str
    message: str


def find_warnings(connection: sqlite3.Connection, metering_point_id: int) -> list[AssignmentWarning]:
    """Detect overlapping or gapped assignment periods for one MeteringPoint."""
    assignments = sorted(list_for_metering_point(connection, metering_point_id), key=lambda a: a.valid_from)
    warnings: list[AssignmentWarning] = []

    for earlier, later in zip(assignments, assignments[1:]):
        earlier_end = earlier.valid_to
        if earlier_end is None or earlier_end >= later.valid_from:
            warnings.append(
                AssignmentWarning(
                    metering_point_id=metering_point_id,
                    kind="overlap",
                    message=(
                        f"Überlappende Zuordnungen bei Messpunkt {metering_point_id}: "
                        f"{earlier.valid_from} - "
                        f"{earlier_end or 'offen'} und ab {later.valid_from}."
                    ),
                )
            )
        else:
            from datetime import timedelta

            if earlier_end + timedelta(days=1) < later.valid_from:
                warnings.append(
                    AssignmentWarning(
                        metering_point_id=metering_point_id,
                        kind="gap",
                        message=(
                            f"Lücke in Zuordnungen bei Messpunkt {metering_point_id}: "
                            f"{earlier_end + timedelta(days=1)} bis "
                            f"{later.valid_from - timedelta(days=1)} ist "
                            "niemandem zugeordnet."
                        ),
                    )
                )

    return warnings
