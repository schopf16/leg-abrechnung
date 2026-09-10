"""Time-bounded assignments of metering points to persons (assignment history).

A MeteringPoint is physically fixed to a site, but the person billed for
it can change over time (e.g. a tenant moving out mid-quarter). Each row
in `assignment` represents one such period; `valid_to = NULL` means
"still valid / open-ended". Moving never changes the MeteringPoint, its
site, or that site's LEG -- only which Person the
Assignment points at.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional


@dataclass
class Assignment:
    """One period during which a MeteringPoint was billed to a given Person.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        person_id: Foreign key to the person.
        metering_point_id: Foreign key to the assigned metering point.
        valid_from: First calendar day (inclusive) this assignment applies.
        valid_to: Last calendar day (inclusive) this assignment applies,
            or `None` if the assignment is open-ended (still current).
        created_at: ISO-8601 creation timestamp.
    """

    id: Optional[int]
    person_id: int
    metering_point_id: int
    valid_from: date
    valid_to: Optional[date]
    created_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Assignment":
        """Build a `Assignment` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `assignment` table.

        Returns:
            The corresponding `Assignment` dataclass instance.
        """
        return Assignment(
            id=row["id"],
            person_id=row["person_id"],
            metering_point_id=row["metering_point_id"],
            valid_from=date.fromisoformat(row["valid_from"]),
            valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
            created_at=row["created_at"],
        )

    def covers(self, moment: datetime) -> bool:
        """Check whether this Assignment is active at a given point in time.

        Strict: requires `valid_from` to have already passed. This is
        what billing/distribution and historical reading-completeness
        checks need -- attributing energy to someone before their
        Assignment's exact start date would be a real correctness bug, see
        `app.domain.distribution` and `app.domain.quality_checks.
        check_reading_completeness`. For "does this person currently (or
        soon) belong here" questions -- a recipient list, a participant
        overview -- see `is_current_or_upcoming` instead: an
        administrator commonly pre-enters a Assignment weeks or months
        ahead of its actual start (e.g. preparing the next quarter's
        move-ins in advance), and such a person should already count
        there, unlike for billing.

        Args:
            moment: Timestamp to test (only its calendar date is compared).

        Returns:
            `True` if `valid_from <= moment.date() <= valid_to` (or
            `valid_to` is `None`, meaning open-ended).
        """
        moment_date = moment.date()
        if moment_date < self.valid_from:
            return False
        if self.valid_to is not None and moment_date > self.valid_to:
            return False
        return True

    def is_current_or_upcoming(self, moment: datetime) -> bool:
        """Whether this Assignment has not ended yet, as of `moment`.

        Unlike `covers`, does NOT require `valid_from` to have already
        passed -- see that method's docstring for why the two need
        different semantics and which callers need which.

        Args:
            moment: Timestamp to test (only its calendar date is compared).

        Returns:
            `True` if `valid_to` is `None` (open-ended) or on/after
            `moment`'s date, regardless of whether `valid_from` has
            started yet.
        """
        return self.valid_to is None or self.valid_to >= moment.date()


def list_for_metering_point(
    connection: sqlite3.Connection, metering_point_id: int
) -> list[Assignment]:
    """List all assignments of a MeteringPoint, most recent `valid_from` first.

    Args:
        connection: Open SQLite connection.
        metering_point_id: Primary key of the metering point.

    Returns:
        All assignments for the metering point, ordered by `valid_from`
        descending.
    """
    rows = connection.execute(
        "SELECT * FROM assignment WHERE metering_point_id = ? ORDER BY valid_from DESC",
        (metering_point_id,),
    ).fetchall()
    return [Assignment.from_row(row) for row in rows]


def get_relevant_for_metering_point(
    connection: sqlite3.Connection, metering_point_id: int, moment: datetime
) -> Optional[Assignment]:
    """The Assignment to show as "currently assigned" for one MeteringPoint.

    Prefers the Assignment that actually `covers` `moment` (already
    started). If none has started yet, falls back to the soonest-starting
    one that `is_current_or_upcoming` -- a not-yet-started assignment
    should still show up here (see the caller in `app.gui.pages.
    metering_points`/`sites`, which marks it visually as upcoming rather
    than hiding it), instead of the MeteringPoint looking unassigned just
    because the administrator entered it ahead of time.

    Args:
        connection: Open SQLite connection.
        metering_point_id: Primary key of the metering point.
        moment: Reference point in time.

    Returns:
        The relevant `Assignment`, or `None` if the MeteringPoint has no
        current-or-upcoming assignment at all.
    """
    candidates = [z for z in list_for_metering_point(connection, metering_point_id) if z.is_current_or_upcoming(moment)]
    for assignment in candidates:
        if assignment.covers(moment):
            return assignment
    if not candidates:
        return None
    return min(candidates, key=lambda z: z.valid_from)


def list_for_person(connection: sqlite3.Connection, person_id: int) -> list[Assignment]:
    """List all assignments of a Person, most recent `valid_from` first.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.

    Returns:
        All assignments for the person, ordered by `valid_from` descending.
    """
    rows = connection.execute(
        "SELECT * FROM assignment WHERE person_id = ? ORDER BY valid_from DESC",
        (person_id,),
    ).fetchall()
    return [Assignment.from_row(row) for row in rows]


def get(connection: sqlite3.Connection, assignment_id: int) -> Optional[Assignment]:
    """Fetch a single Assignment by id.

    Args:
        connection: Open SQLite connection.
        assignment_id: Primary key of the assignment.

    Returns:
        The matching `Assignment`, or `None` if no such id exists.
    """
    row = connection.execute(
        "SELECT * FROM assignment WHERE id = ?", (assignment_id,)
    ).fetchone()
    return Assignment.from_row(row) if row else None


def list_all(connection: sqlite3.Connection) -> list[Assignment]:
    """List every Assignment in the database.

    Args:
        connection: Open SQLite connection.

    Returns:
        All assignments, ordered by MeteringPoint id and `valid_from`.
    """
    rows = connection.execute(
        "SELECT * FROM assignment ORDER BY metering_point_id, valid_from"
    ).fetchall()
    return [Assignment.from_row(row) for row in rows]


def create(connection: sqlite3.Connection, assignment: Assignment) -> int:
    """Insert a new Assignment.

    Args:
        connection: Open SQLite connection.
        assignment: Data to insert; `id` and `created_at` are ignored and
            generated by this function.

    Returns:
        The primary key of the newly created assignment.
    """
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
    """Update an existing Assignment's validity period or person.

    Args:
        connection: Open SQLite connection.
        assignment: Assignment with `id` set to an existing record.

    Returns:
        None.

    Raises:
        ValueError: If `assignment.id` is `None`.
    """
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
    """Delete a Assignment.

    Args:
        connection: Open SQLite connection.
        assignment_id: Primary key of the assignment to delete.

    Returns:
        None.
    """
    connection.execute("DELETE FROM assignment WHERE id = ?", (assignment_id,))
    connection.commit()


@dataclass
class AssignmentWarning:
    """A detected problem in a MeteringPoint's assignment history.

    Attributes:
        metering_point_id: Metering point the warning refers to.
        kind: Either "overlap" (two assignments cover the same day) or
            "gap" (a day between assignments belongs to nobody).
        message: Human-readable (German) description for display in the UI.
    """

    metering_point_id: int
    kind: str
    message: str


def find_warnings(
    connection: sqlite3.Connection, metering_point_id: int
) -> list[AssignmentWarning]:
    """Detect overlapping or gapped assignment periods for one MeteringPoint.

    Assignments are checked pairwise after sorting by `valid_from`: any
    two consecutive periods that overlap, or that leave a day uncovered
    between them, produce a warning. Open-ended assignments
    (`valid_to is None`) are only allowed to be the last one; an
    earlier open-ended assignment is reported as an overlap with
    everything that follows it.

    Args:
        connection: Open SQLite connection.
        metering_point_id: Primary key of the metering point to check.

    Returns:
        A list of `AssignmentWarning`, empty if the history is consistent.
    """
    assignments = sorted(
        list_for_metering_point(connection, metering_point_id), key=lambda a: a.valid_from
    )
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
