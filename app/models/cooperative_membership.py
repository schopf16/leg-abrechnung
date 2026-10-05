"""Time-bounded Genossenschaft memberships with their share count (history)."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional


@dataclass
class CooperativeMembership:
    """One period during which a Person held a given number of shares."""

    id: Optional[int]
    person_id: int
    shares: int
    valid_from: date
    valid_to: Optional[date]
    created_at: str

    @property
    def is_open(self) -> bool:
        """Whether this period has no end date yet."""
        return self.valid_to is None

    def covers(self, day: date) -> bool:
        """Whether the membership was in force on a given calendar day."""
        if day < self.valid_from:
            return False
        if self.valid_to is not None and day > self.valid_to:
            return False
        return True

    @staticmethod
    def from_row(row: sqlite3.Row) -> "CooperativeMembership":
        """Build a `CooperativeMembership` from a `sqlite3.Row`."""
        return CooperativeMembership(
            id=row["id"],
            person_id=row["person_id"],
            shares=row["shares"],
            valid_from=date.fromisoformat(row["valid_from"]),
            valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
            created_at=row["created_at"],
        )


def get(connection: sqlite3.Connection, membership_id: int) -> Optional[CooperativeMembership]:
    """Fetch a single membership period by id."""
    row = connection.execute("SELECT * FROM cooperative_membership WHERE id = ?", (membership_id,)).fetchone()
    return CooperativeMembership.from_row(row) if row else None


def list_for_person(connection: sqlite3.Connection, person_id: int) -> list[CooperativeMembership]:
    """List one Person's membership periods, most recent `valid_from` first."""
    rows = connection.execute(
        "SELECT * FROM cooperative_membership WHERE person_id = ? ORDER BY valid_from DESC",
        (person_id,),
    ).fetchall()
    return [CooperativeMembership.from_row(row) for row in rows]


def list_all(connection: sqlite3.Connection) -> list[CooperativeMembership]:
    """List every membership period in the database."""
    rows = connection.execute(
        "SELECT * FROM cooperative_membership ORDER BY person_id, valid_from"
    ).fetchall()
    return [CooperativeMembership.from_row(row) for row in rows]


def current_for_person(
    connection: sqlite3.Connection, person_id: int, day: Optional[date] = None
) -> Optional[CooperativeMembership]:
    """The membership period in force for one Person on a given day."""
    reference = day or date.today()
    for membership in list_for_person(connection, person_id):
        if membership.covers(reference):
            return membership
    return None


def shares_for_person(connection: sqlite3.Connection, person_id: int, day: Optional[date] = None) -> int:
    """How many shares one Person held on a given day."""
    membership = current_for_person(connection, person_id, day)
    return membership.shares if membership else 0


def member_person_ids(connection: sqlite3.Connection, day: Optional[date] = None) -> set[int]:
    """The ids of everyone who was a member on a given day."""
    reference = day or date.today()
    return {m.person_id for m in list_all(connection) if m.covers(reference)}


def create(connection: sqlite3.Connection, membership: CooperativeMembership) -> int:
    """Insert a new membership period."""
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
    """Update an existing membership period."""
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
    """Delete one membership period."""
    connection.execute("DELETE FROM cooperative_membership WHERE id = ?", (membership_id,))
    connection.commit()


@dataclass
class MembershipWarning:
    """A contradiction in one Person's membership history."""

    person_id: int
    kind: str
    message: str


def find_warnings(connection: sqlite3.Connection, person_id: int) -> list[MembershipWarning]:
    """Detect overlapping membership periods for one Person."""
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
