"""Tracks a Person's progress through the real-world steps of a LEG
membership ending -- the reverse of `app.models.person_onboarding`, with
exactly the same structure (a fixed, unordered set of step dates).

Two triggers, one process, distinguished by `grund`:
  - `"zahlungsverzug"`: prepared (never auto-started) from the Mahnwesen
    once a person's 2. Mahnung is sent without payment following -- see
    `app.domain.mahnwesen` and `app.gui.pages.mahnwesen`. Michael still
    confirms the actual start himself.
  - `"freiwillig"`: a normal voluntary exit (member resigns, moves away),
    started manually, exactly like a manual "+ Aufnahme starten".
  - `"sonstig"`: anything else.

Ending a membership here never touches the Debitoren claim: a Person's
Saldo (`app.models.account_entry`) is entirely independent of this table.
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
    ("beschlossen_am", "Austritt/Ausschluss beschlossen"),
    ("messpunkt_austritt_am", "Austrittsdatum Messpunkt festgelegt"),
    ("bkw_informiert_am", "BKW informiert"),
    ("person_bestaetigt_am", "Person schriftlich bestätigt"),
]

#: Valid values for `PersonOffboarding.grund`.
GRUND_OPTIONS: dict[str, str] = {
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
        grund: `"zahlungsverzug"`, `"freiwillig"` or `"sonstig"`.
        beschlossen_am: Date of step 1, "Austritt/Ausschluss beschlossen",
            or `None`.
        messpunkt_austritt_am: Date of step 2, "Austrittsdatum Messpunkt
            festgelegt", or `None`. Setting this in the GUI offers to end
            the person's currently open `Zuordnung`(en) with this date as
            `gueltig_bis` -- never automatic, see `app.gui.pages.austritte`.
        bkw_informiert_am: Date of step 3, "BKW informiert", or `None`.
        person_bestaetigt_am: Date of step 4, "Person schriftlich
            bestätigt", or `None`.
        created_at: ISO-8601 timestamp tracking was started for this Person.
    """

    id: Optional[int]
    person_id: int
    grund: str
    beschlossen_am: Optional[date]
    messpunkt_austritt_am: Optional[date]
    bkw_informiert_am: Optional[date]
    person_bestaetigt_am: Optional[date]
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
            grund=row["grund"],
            beschlossen_am=_date(row["beschlossen_am"]),
            messpunkt_austritt_am=_date(row["messpunkt_austritt_am"]),
            bkw_informiert_am=_date(row["bkw_informiert_am"]),
            person_bestaetigt_am=_date(row["person_bestaetigt_am"]),
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
    grund: str,
    beschlossen_am: Optional[date] = None,
) -> PersonOffboarding:
    """Start offboarding tracking for a Person, or return its existing tracker.

    Idempotent, same rationale as `person_onboarding.start_for_person`.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person to start tracking for.
        grund: `"zahlungsverzug"`, `"freiwillig"` or `"sonstig"` -- only
            used if a tracker does not already exist.
        beschlossen_am: Date to record for step 1, or `None` to leave it
            unset for now.

    Returns:
        The (possibly pre-existing) `PersonOffboarding` for this Person.
    """
    existing = get_by_person(connection, person_id)
    if existing is not None:
        return existing

    cursor = connection.execute(
        """
        INSERT INTO person_offboarding (person_id, grund, beschlossen_am, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            person_id,
            grund,
            beschlossen_am.isoformat() if beschlossen_am else None,
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
            grund = ?, beschlossen_am = ?, messpunkt_austritt_am = ?,
            bkw_informiert_am = ?, person_bestaetigt_am = ?
        WHERE id = ?
        """,
        (
            offboarding.grund,
            offboarding.beschlossen_am.isoformat() if offboarding.beschlossen_am else None,
            offboarding.messpunkt_austritt_am.isoformat() if offboarding.messpunkt_austritt_am else None,
            offboarding.bkw_informiert_am.isoformat() if offboarding.bkw_informiert_am else None,
            offboarding.person_bestaetigt_am.isoformat() if offboarding.person_bestaetigt_am else None,
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
