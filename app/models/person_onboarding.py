"""Tracks a Person's progress through the real-world steps between an
interested party's registration and full LEG membership.

Deliberately a separate, optional table rather than columns on `Person`:
a tracking row only exists once explicitly started (auto-started when a
Web-Registrierung is taken over via "Person übernehmen", see
`app.gui.pages.web_registrierungen`, or manually via `start_for_person`),
so a Person who joined before this feature existed -- or was never routed
through this pipeline -- never retroactively appears as having an
overdue step.

The five steps are fixed and always presented in this order, but nothing
here enforces that they are actually completed in order -- the
administrator records whatever date applies, whenever it happens.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

#: The five onboarding steps, in display order, as
#: `(PersonOnboarding attribute name, German label)` pairs.
STEPS: list[tuple[str, str]] = [
    ("registered_at", "Anmeldung bei uns"),
    ("leg_assigned_at", "Einteilung in LEG"),
    ("contract_signed_at", "Gesellschaftsvertrag unterzeichnet"),
    ("bkw_registered_at", "Anmeldung bei der BKW"),
    ("bkw_confirmed_at", "Bestätigung durch die BKW"),
]


@dataclass
class PersonOnboarding:
    """One Person's progress through the five onboarding steps.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        person_id: The Person being tracked (one tracking row per Person).
        registered_at: Date of step 1, "Anmeldung bei uns", or `None`.
        leg_assigned_at: Date of step 2, "Einteilung in LEG", or `None`.
        leg_id: The LEG assigned in step 2, or `None` until decided. Kept
            even if that LEG is later deleted (`ON DELETE SET NULL`) --
            losing the LEG record must never destroy onboarding history.
        contract_signed_at: Date of step 3, "Gesellschaftsvertrag
            unterzeichnet", or `None`.
        bkw_registered_at: Date of step 4, "Anmeldung bei der BKW", or `None`.
        bkw_confirmed_at: Date of step 5, "Bestätigung durch die BKW",
            or `None`.
        created_at: ISO-8601 timestamp tracking was started for this
            Person -- also the reference point for how long step 1 has
            been open if `registered_at` itself is not yet set.
    """

    id: Optional[int]
    person_id: int
    registered_at: Optional[date]
    leg_assigned_at: Optional[date]
    leg_id: Optional[int]
    contract_signed_at: Optional[date]
    bkw_registered_at: Optional[date]
    bkw_confirmed_at: Optional[date]
    created_at: str

    @property
    def is_complete(self) -> bool:
        """Whether every step has a date, i.e. onboarding is finished.

        Returns:
            `True` if all five step dates are set.
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
            The reference date `days_open`/`is_overdue` measure from.
            Meaningless (but still returns the last step's date) if
            `is_complete`.
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
            `is_complete` (nothing is "open" anymore).
        """
        if self.is_complete:
            return None
        return ((reference or date.today()) - self.current_step_since).days

    def is_overdue(self, threshold_days: int, reference: Optional[date] = None) -> bool:
        """Whether the current step has been open for too long.

        Args:
            threshold_days: Number of days after which an open step
                counts as overdue (see `LegSettings.onboarding_overdue_days`).
            reference: Day to measure against, defaults to today.

        Returns:
            `True` if not yet complete and `days_open >= threshold_days`.
        """
        days = self.days_open(reference)
        return days is not None and days >= threshold_days

    @staticmethod
    def from_row(row: sqlite3.Row) -> "PersonOnboarding":
        """Build a `PersonOnboarding` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `person_onboarding` table.

        Returns:
            The corresponding `PersonOnboarding` dataclass instance.
        """

        def _date(value: Optional[str]) -> Optional[date]:
            return date.fromisoformat(value) if value else None

        return PersonOnboarding(
            id=row["id"],
            person_id=row["person_id"],
            registered_at=_date(row["registered_at"]),
            leg_assigned_at=_date(row["leg_assigned_at"]),
            leg_id=row["leg_id"],
            contract_signed_at=_date(row["contract_signed_at"]),
            bkw_registered_at=_date(row["bkw_registered_at"]),
            bkw_confirmed_at=_date(row["bkw_confirmed_at"]),
            created_at=row["created_at"],
        )


def get(connection: sqlite3.Connection, onboarding_id: int) -> Optional[PersonOnboarding]:
    """Fetch a single onboarding tracker by id.

    Args:
        connection: Open SQLite connection.
        onboarding_id: Primary key of the tracker.

    Returns:
        The matching `PersonOnboarding`, or `None` if no such id exists.
    """
    row = connection.execute(
        "SELECT * FROM person_onboarding WHERE id = ?", (onboarding_id,)
    ).fetchone()
    return PersonOnboarding.from_row(row) if row else None


def get_by_person(connection: sqlite3.Connection, person_id: int) -> Optional[PersonOnboarding]:
    """Fetch the onboarding tracker for a Person, if tracking was started.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.

    Returns:
        The matching `PersonOnboarding`, or `None` if this Person has no
        tracker (never started, or not routed through this pipeline).
    """
    row = connection.execute(
        "SELECT * FROM person_onboarding WHERE person_id = ?", (person_id,)
    ).fetchone()
    return PersonOnboarding.from_row(row) if row else None


def list_all(connection: sqlite3.Connection) -> list[PersonOnboarding]:
    """List every onboarding tracker, oldest first.

    Args:
        connection: Open SQLite connection.

    Returns:
        All trackers, ordered by `created_at`.
    """
    rows = connection.execute(
        "SELECT * FROM person_onboarding ORDER BY created_at"
    ).fetchall()
    return [PersonOnboarding.from_row(row) for row in rows]


def list_in_progress(connection: sqlite3.Connection) -> list[PersonOnboarding]:
    """List onboarding trackers that are not yet complete, oldest first.

    Args:
        connection: Open SQLite connection.

    Returns:
        Trackers with at least one step date still missing, ordered by
        `created_at`. Filtered in Python (not SQL) since "complete" reads
        across five nullable columns -- the table is expected to stay
        small (interested parties currently being onboarded, not the
        full membership).
    """
    return [o for o in list_all(connection) if not o.is_complete]


def start_for_person(
    connection: sqlite3.Connection, person_id: int, registered_at: Optional[date] = None
) -> PersonOnboarding:
    """Start onboarding tracking for a Person, or return its existing tracker.

    Idempotent so both the automatic trigger (taking over a Web-
    Registrierung) and the manual "+ Aufnahme starten" action can call
    this without first checking whether tracking already exists.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person to start tracking for.
        registered_at: Date to record for step 1 ("Anmeldung bei uns"),
            e.g. the originating Web-Registrierung's submission date.
            Left unset (`None`) if not given -- the administrator can
            fill it in later like any other step.

    Returns:
        The (possibly pre-existing) `PersonOnboarding` for this Person.
    """
    existing = get_by_person(connection, person_id)
    if existing is not None:
        return existing

    cursor = connection.execute(
        """
        INSERT INTO person_onboarding (person_id, registered_at, created_at)
        VALUES (?, ?, ?)
        """,
        (
            person_id,
            registered_at.isoformat() if registered_at else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return get(connection, cursor.lastrowid)


def update(connection: sqlite3.Connection, onboarding: PersonOnboarding) -> None:
    """Update an existing onboarding tracker's step dates and LEG.

    Args:
        connection: Open SQLite connection.
        onboarding: Tracker with `id` set to an existing record.

    Returns:
        None.

    Raises:
        ValueError: If `onboarding.id` is `None`.
    """
    if onboarding.id is None:
        raise ValueError("Cannot update a PersonOnboarding without an id.")
    connection.execute(
        """
        UPDATE person_onboarding SET
            registered_at = ?, leg_assigned_at = ?, leg_id = ?,
            contract_signed_at = ?, bkw_registered_at = ?, bkw_confirmed_at = ?
        WHERE id = ?
        """,
        (
            onboarding.registered_at.isoformat() if onboarding.registered_at else None,
            onboarding.leg_assigned_at.isoformat() if onboarding.leg_assigned_at else None,
            onboarding.leg_id,
            onboarding.contract_signed_at.isoformat()
            if onboarding.contract_signed_at
            else None,
            onboarding.bkw_registered_at.isoformat() if onboarding.bkw_registered_at else None,
            onboarding.bkw_confirmed_at.isoformat() if onboarding.bkw_confirmed_at else None,
            onboarding.id,
        ),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, onboarding_id: int) -> None:
    """Discard an onboarding tracker -- never deletes the Person itself.

    Args:
        connection: Open SQLite connection.
        onboarding_id: Primary key of the tracker to delete.

    Returns:
        None.
    """
    connection.execute("DELETE FROM person_onboarding WHERE id = ?", (onboarding_id,))
    connection.commit()
