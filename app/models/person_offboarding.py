"""Tracks a Person's progress through the real-world steps of a LEG
membership ending -- the reverse of `app.models.person_onboarding`, with
exactly the same structure (a fixed, unordered set of step dates).

Two triggers, one process, distinguished by `reason`:
  - `"zahlungsverzug"`: prepared (never auto-started) from the dunning
    once a person's 2. dunning notice is sent without payment following -- see
    `app.domain.dunning` and `app.gui.pages.dunning`. Michael still
    confirms the actual start himself.
  - `"freiwillig"`: a normal voluntary exit (member resigns, moves away),
    started manually, exactly like a manual "+ Aufnahme starten".
  - `"sonstig"`: anything else.

Ending a membership here never touches the receivables claim: a Person's
balance (`app.models.account_entry`) is entirely independent of this table.
Someone excluded for non-payment still owes what they owed.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

#: The four offboarding steps, in display order, as
#: `(PersonOffboarding attribute name, German label)` pairs -- mirrors
#: `app.models.person_onboarding.STEPS`.
STEPS: list[tuple[str, str]] = [
    ("decided_at", "Austritt/Ausschluss beschlossen"),
    ("metering_point_exit_at", "Austrittsdatum Messpunkt festgelegt"),
    ("bkw_informed_at", "BKW informiert"),
    ("person_confirmed_at", "Person schriftlich bestätigt"),
]

#: Valid values for `PersonOffboarding.reason`.
REASON_OPTIONS: dict[str, str] = {
    "zahlungsverzug": "Zahlungsverzug",
    "freiwillig": "Freiwilliger Austritt",
    "sonstig": "Sonstiges",
}


@dataclass
class PersonOffboarding:
    """One Person's progress through the four offboarding steps.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        person_id: The Person being tracked (one tracking row per Person).
        reason: `"zahlungsverzug"`, `"freiwillig"` or `"sonstig"`.
        decided_at: Date of step 1, "Austritt/Ausschluss beschlossen",
            or `None`.
        metering_point_exit_at: Date of step 2, "Austrittsdatum MeteringPoint
            festgelegt", or `None`. Setting this in the GUI offers to end
            the person's currently open `Assignment`(en) with this date as
            `valid_to` -- never automatic, see `app.gui.pages.offboardings`.
        bkw_informed_at: Date of step 3, "BKW informiert", or `None`.
        person_confirmed_at: Date of step 4, "Person schriftlich
            bestätigt", or `None`.
        created_at: ISO-8601 timestamp tracking was started for this Person.
    """

    id: Optional[int]
    person_id: int
    reason: str
    decided_at: Optional[date]
    metering_point_exit_at: Optional[date]
    bkw_informed_at: Optional[date]
    person_confirmed_at: Optional[date]
    created_at: str

    @property
    def is_complete(self) -> bool:
        """Whether every step has a date, i.e. the process is finished.

        Returns:
            `True` if all four step dates are set.
        """
        return all(getattr(self, attr) is not None for attr, _ in STEPS)

    @property
    def current_step(self) -> Optional[tuple[str, str]]:
        """The first step that has no date yet.

        Returns:
            The `(attribute_name, label)` pair for the first incomplete
            step in `STEPS` order, or `None` if `is_complete`.
        """
        for attr, label in STEPS:
            if getattr(self, attr) is None:
                return attr, label
        return None

    @property
    def current_step_since(self) -> date:
        """The date the current step became active.

        This is the previous step's date, or -- if the current step is
        the first one -- the day tracking was started (`created_at`).

        Returns:
            The reference date `days_open` measures from.
        """
        previous_date: Optional[date] = None
        for attr, _ in STEPS:
            value = getattr(self, attr)
            if value is None:
                break
            previous_date = value
        if previous_date is not None:
            return previous_date
        return datetime.fromisoformat(self.created_at).date()

    def days_open(self, reference: Optional[date] = None) -> Optional[int]:
        """How many days the current step has been open.

        Args:
            reference: Day to measure against, defaults to today.

        Returns:
            The number of days since `current_step_since`, or `None` if
            `is_complete`.
        """
        if self.is_complete:
            return None
        return ((reference or date.today()) - self.current_step_since).days

    @staticmethod
    def from_row(row: sqlite3.Row) -> "PersonOffboarding":
        """Build a `PersonOffboarding` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `person_offboarding` table.

        Returns:
            The corresponding `PersonOffboarding` dataclass instance.
        """

        def _date(value: Optional[str]) -> Optional[date]:
            return date.fromisoformat(value) if value else None

        return PersonOffboarding(
            id=row["id"],
            person_id=row["person_id"],
            reason=row["reason"],
            decided_at=_date(row["decided_at"]),
            metering_point_exit_at=_date(row["metering_point_exit_at"]),
            bkw_informed_at=_date(row["bkw_informed_at"]),
            person_confirmed_at=_date(row["person_confirmed_at"]),
            created_at=row["created_at"],
        )


def get(connection: sqlite3.Connection, offboarding_id: int) -> Optional[PersonOffboarding]:
    """Fetch a single offboarding tracker by id.

    Args:
        connection: Open SQLite connection.
        offboarding_id: Primary key of the tracker.

    Returns:
        The matching `PersonOffboarding`, or `None` if no such id exists.
    """
    row = connection.execute(
        "SELECT * FROM person_offboarding WHERE id = ?", (offboarding_id,)
    ).fetchone()
    return PersonOffboarding.from_row(row) if row else None


def get_by_person(connection: sqlite3.Connection, person_id: int) -> Optional[PersonOffboarding]:
    """Fetch the offboarding tracker for a Person, if tracking was started.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.

    Returns:
        The matching `PersonOffboarding`, or `None` if this Person has no
        tracker.
    """
    row = connection.execute(
        "SELECT * FROM person_offboarding WHERE person_id = ?", (person_id,)
    ).fetchone()
    return PersonOffboarding.from_row(row) if row else None


def list_all(connection: sqlite3.Connection) -> list[PersonOffboarding]:
    """List every offboarding tracker, oldest first.

    Args:
        connection: Open SQLite connection.

    Returns:
        All trackers, ordered by `created_at`.
    """
    rows = connection.execute(
        "SELECT * FROM person_offboarding ORDER BY created_at"
    ).fetchall()
    return [PersonOffboarding.from_row(row) for row in rows]


def list_in_progress(connection: sqlite3.Connection) -> list[PersonOffboarding]:
    """List offboarding trackers that are not yet complete, oldest first.

    Args:
        connection: Open SQLite connection.

    Returns:
        Trackers with at least one step date still missing, ordered by
        `created_at`. Filtered in Python, same rationale as
        `person_onboarding.list_in_progress`.
    """
    return [o for o in list_all(connection) if not o.is_complete]


def start_for_person(
    connection: sqlite3.Connection,
    person_id: int,
    *,
    reason: str,
    decided_at: Optional[date] = None,
) -> PersonOffboarding:
    """Start offboarding tracking for a Person, or return its existing tracker.

    Idempotent, same rationale as `person_onboarding.start_for_person`.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person to start tracking for.
        reason: `"zahlungsverzug"`, `"freiwillig"` or `"sonstig"` -- only
            used if a tracker does not already exist.
        decided_at: Date to record for step 1, or `None` to leave it
            unset for now.

    Returns:
        The (possibly pre-existing) `PersonOffboarding` for this Person.
    """
    existing = get_by_person(connection, person_id)
    if existing is not None:
        return existing

    cursor = connection.execute(
        """
        INSERT INTO person_offboarding (person_id, reason, decided_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            person_id,
            reason,
            decided_at.isoformat() if decided_at else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return get(connection, cursor.lastrowid)


def update(connection: sqlite3.Connection, offboarding: PersonOffboarding) -> None:
    """Update an existing offboarding tracker's step dates.

    Args:
        connection: Open SQLite connection.
        offboarding: Tracker with `id` set to an existing record.

    Returns:
        None.

    Raises:
        ValueError: If `offboarding.id` is `None`.
    """
    if offboarding.id is None:
        raise ValueError("Cannot update a PersonOffboarding without an id.")
    connection.execute(
        """
        UPDATE person_offboarding SET
            reason = ?, decided_at = ?, metering_point_exit_at = ?,
            bkw_informed_at = ?, person_confirmed_at = ?
        WHERE id = ?
        """,
        (
            offboarding.reason,
            offboarding.decided_at.isoformat() if offboarding.decided_at else None,
            offboarding.metering_point_exit_at.isoformat() if offboarding.metering_point_exit_at else None,
            offboarding.bkw_informed_at.isoformat() if offboarding.bkw_informed_at else None,
            offboarding.person_confirmed_at.isoformat() if offboarding.person_confirmed_at else None,
            offboarding.id,
        ),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, offboarding_id: int) -> None:
    """Discard an offboarding tracker -- never deletes the Person itself.

    Args:
        connection: Open SQLite connection.
        offboarding_id: Primary key of the tracker to delete.

    Returns:
        None.
    """
    connection.execute("DELETE FROM person_offboarding WHERE id = ?", (offboarding_id,))
    connection.commit()
