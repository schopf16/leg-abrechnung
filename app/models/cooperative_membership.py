"""Time-bounded Genossenschaft memberships with their share count (history).

The LEG is becoming a cooperative. Not every participant becomes a member --
some simply use the service -- so membership is a property of its own, and
"who held how many shares when" is a question a cooperative has to answer
years later, at an assembly or in an audit. That makes this a dated relation
in the same shape as `app.models.assignment`, not a pair of columns on
`person`: changing a share count closes the running row and opens a new one,
so the previous figure survives.

Two deliberate differences from `Assignment`:

  - **A gap is not a problem here.** Someone who leaves and rejoins years
    later has one by rights, and it is the truth about their membership.
    `find_warnings` therefore reports only overlaps -- two different share
    counts on the same day is a contradiction, a year without membership is
    not.
  - **"Member" means today, strictly.** `Assignment` counts a period
    pre-entered ahead of its start (`is_current_or_upcoming`), because there
    the question is about planning. Here it would be wrong: a cooperative
    has a membership roll at a given moment, and mailing the members must
    not reach somebody who has not joined yet. So everything member-facing
    goes through `covers(today)`.

Share counts carry no nominal value -- only how many. The cooperative's
capital is not this app's business.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional


@dataclass
class CooperativeMembership:
    """One period during which a Person held a given number of shares.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        person_id: Foreign key to the member.
        shares: Number of shares held during this period, never negative.
            Zero is allowed and means "member, shares not (yet) recorded" --
            flagged on the dashboard rather than rejected, because the
            membership is a fact even while the paperwork lags.
        valid_from: First calendar day (inclusive) of this period.
        valid_to: Last calendar day (inclusive), or `None` while the
            membership is running.
        created_at: ISO-8601 creation timestamp.
    """

    id: Optional[int]
    person_id: int
    shares: int
    valid_from: date
    valid_to: Optional[date]
    created_at: str

    @property
    def is_open(self) -> bool:
        """Whether this period has no end date yet.

        Returns:
            `True` if `valid_to` is `None`.
        """
        return self.valid_to is None

    def covers(self, day: date) -> bool:
        """Whether the membership was in force on a given calendar day.

        Strict at both ends: a period starting tomorrow does not cover
        today. See the module docstring for why this differs from
        `Assignment.is_current_or_upcoming`.

        Args:
            day: The calendar day to test.

        Returns:
            `True` if `valid_from <= day <= valid_to`, treating a `None`
            `valid_to` as open-ended.
        """
        if day < self.valid_from:
            return False
        if self.valid_to is not None and day > self.valid_to:
            return False
        return True

    @staticmethod
    def from_row(row: sqlite3.Row) -> "CooperativeMembership":
        """Build a `CooperativeMembership` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `cooperative_membership` table.

        Returns:
            The corresponding `CooperativeMembership` dataclass instance.
        """
        return CooperativeMembership(
            id=row["id"],
            person_id=row["person_id"],
            shares=row["shares"],
            valid_from=date.fromisoformat(row["valid_from"]),
            valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
            created_at=row["created_at"],
        )


def get(connection: sqlite3.Connection, membership_id: int) -> Optional[CooperativeMembership]:
    """Fetch a single membership period by id.

    Args:
        connection: Open SQLite connection.
        membership_id: Primary key of the membership period.

    Returns:
        The matching `CooperativeMembership`, or `None` if no such id exists.
    """
    row = connection.execute("SELECT * FROM cooperative_membership WHERE id = ?", (membership_id,)).fetchone()
    return CooperativeMembership.from_row(row) if row else None


def list_for_person(connection: sqlite3.Connection, person_id: int) -> list[CooperativeMembership]:
    """List one Person's membership periods, most recent `valid_from` first.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.

    Returns:
        All membership periods of this person, newest first -- the order the
        detail page shows them in.
    """
    rows = connection.execute(
        "SELECT * FROM cooperative_membership WHERE person_id = ? ORDER BY valid_from DESC",
        (person_id,),
    ).fetchall()
    return [CooperativeMembership.from_row(row) for row in rows]


def list_all(connection: sqlite3.Connection) -> list[CooperativeMembership]:
    """List every membership period in the database.

    Args:
        connection: Open SQLite connection.

    Returns:
        All periods, ordered by person and `valid_from`.
    """
    rows = connection.execute(
        "SELECT * FROM cooperative_membership ORDER BY person_id, valid_from"
    ).fetchall()
    return [CooperativeMembership.from_row(row) for row in rows]


def current_for_person(
    connection: sqlite3.Connection, person_id: int, day: Optional[date] = None
) -> Optional[CooperativeMembership]:
    """The membership period in force for one Person on a given day.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.
        day: Reference day, `None` for today.

    Returns:
        The covering `CooperativeMembership`, or `None` if this person was
        not a member on that day. If overlapping periods exist (a
        contradiction `find_warnings` reports), the most recently started
        one wins -- `list_for_person` is ordered newest first.
    """
    reference = day or date.today()
    for membership in list_for_person(connection, person_id):
        if membership.covers(reference):
            return membership
    return None


def shares_for_person(connection: sqlite3.Connection, person_id: int, day: Optional[date] = None) -> int:
    """How many shares one Person held on a given day.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.
        day: Reference day, `None` for today.

    Returns:
        The share count, or `0` if this person was not a member that day.
        A non-member and a member with no shares recorded both read `0`
        here, so callers that need to tell them apart ask
        `current_for_person` instead.
    """
    membership = current_for_person(connection, person_id, day)
    return membership.shares if membership else 0


def member_person_ids(connection: sqlite3.Connection, day: Optional[date] = None) -> set[int]:
    """The ids of everyone who was a member on a given day.

    Args:
        connection: Open SQLite connection.
        day: Reference day, `None` for today.

    Returns:
        Person ids with a membership period covering that day. Used for the
        "Genossenschafter" filter and the members' mailing list, both of
        which mean today's roll -- see the module docstring.
    """
    reference = day or date.today()
    return {m.person_id for m in list_all(connection) if m.covers(reference)}


def create(connection: sqlite3.Connection, membership: CooperativeMembership) -> int:
    """Insert a new membership period.

    Args:
        connection: Open SQLite connection.
        membership: Data to insert; `id` and `created_at` are ignored and
            generated here.

    Returns:
        The primary key of the newly created period.
    """
    cursor = connection.execute(
        """
        INSERT INTO cooperative_membership (person_id, shares, valid_from, valid_to, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            membership.person_id,
            membership.shares,
            membership.valid_from.isoformat(),
            membership.valid_to.isoformat() if membership.valid_to else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, membership: CooperativeMembership) -> None:
    """Update an existing membership period.

    Args:
        connection: Open SQLite connection.
        membership: Period with `id` set to an existing record.

    Returns:
        None.

    Raises:
        ValueError: If `membership.id` is `None`.
    """
    if membership.id is None:
        raise ValueError("Cannot update a CooperativeMembership without an id.")
    connection.execute(
        """
        UPDATE cooperative_membership SET
            person_id = ?, shares = ?, valid_from = ?, valid_to = ?
        WHERE id = ?
        """,
        (
            membership.person_id,
            membership.shares,
            membership.valid_from.isoformat(),
            membership.valid_to.isoformat() if membership.valid_to else None,
            membership.id,
        ),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, membership_id: int) -> None:
    """Delete one membership period.

    Args:
        connection: Open SQLite connection.
        membership_id: Primary key of the period to delete.

    Returns:
        None.
    """
    connection.execute("DELETE FROM cooperative_membership WHERE id = ?", (membership_id,))
    connection.commit()


@dataclass
class MembershipWarning:
    """A contradiction in one Person's membership history.

    Attributes:
        person_id: Person the warning refers to.
        kind: Always `"overlap"` today. A gap is legitimate here (see the
            module docstring), so there is no "gap" kind -- the field
            exists to match `app.models.assignment.AssignmentWarning`'s
            shape and to leave room if another kind ever turns up.
        message: Human-readable (German) description for the UI.
    """

    person_id: int
    kind: str
    message: str


def find_warnings(connection: sqlite3.Connection, person_id: int) -> list[MembershipWarning]:
    """Detect overlapping membership periods for one Person.

    Only overlaps: two periods covering the same day state two different
    share counts at once, which cannot both be true. Gaps are deliberately
    not reported -- leaving and rejoining is normal, and a warning about it
    would train the administrator to ignore this list.

    An open-ended period is only allowed to be the last one; an earlier one
    left open overlaps everything after it and is reported as such.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person to check.

    Returns:
        A list of `MembershipWarning`, empty if the history is consistent.
    """
    memberships = sorted(list_for_person(connection, person_id), key=lambda m: m.valid_from)
    warnings: list[MembershipWarning] = []
    for earlier, later in zip(memberships, memberships[1:]):
        if earlier.valid_to is None or earlier.valid_to >= later.valid_from:
            warnings.append(
                MembershipWarning(
                    person_id=person_id,
                    kind="overlap",
                    message=(
                        "Überlappende Genossenschafts-Zeiträume: "
                        f"{earlier.valid_from.strftime('%d.%m.%Y')} - "
                        f"{earlier.valid_to.strftime('%d.%m.%Y') if earlier.valid_to else 'offen'} "
                        f"und ab {later.valid_from.strftime('%d.%m.%Y')}."
                    ),
                )
            )
    return warnings
