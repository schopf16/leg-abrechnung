"""Sent-history log for Mahnungen (see `app.domain.mahnwesen`), analogous
to `app.models.email_log.EmailBroadcastLog` for broadcast emails.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class MahnungLog:
    """One completed Mahnung send (a single, possibly consolidated letter
    covering one or more open billing run items for one Person).

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        sent_at: ISO-8601 timestamp the send completed.
        person_id: The Person this Mahnung was sent to.
        stufe: `1` or `2`, the stage reached by this send.
        betrag_rappen: The total amount the Mahnung stated as owed, at
            the moment it was sent.
        billing_run_item_ids: The billing run items this Mahnung covered.
    """

    id: int | None
    sent_at: str
    person_id: int
    stufe: int
    betrag_rappen: int
    billing_run_item_ids: list[int]

    @staticmethod
    def from_row(row: sqlite3.Row) -> "MahnungLog":
        """Build a `MahnungLog` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `mahnung_log` table.

        Returns:
            The corresponding `MahnungLog` dataclass instance.
        """
        return MahnungLog(
            id=row["id"],
            sent_at=row["sent_at"],
            person_id=row["person_id"],
            stufe=row["stufe"],
            betrag_rappen=row["betrag_rappen"],
            billing_run_item_ids=json.loads(row["billing_run_item_ids"]),
        )


def create(
    connection: sqlite3.Connection,
    *,
    person_id: int,
    stufe: int,
    betrag_rappen: int,
    billing_run_item_ids: list[int],
) -> int:
    """Record one completed Mahnung send.

    Args:
        connection: Open SQLite connection.
        person_id: The Person this Mahnung was sent to.
        stufe: `1` or `2`.
        betrag_rappen: The total amount stated as owed.
        billing_run_item_ids: The billing run items covered.

    Returns:
        The primary key of the new log entry.
    """
    cursor = connection.execute(
        """
        INSERT INTO mahnung_log (sent_at, person_id, stufe, betrag_rappen, billing_run_item_ids)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            person_id,
            stufe,
            betrag_rappen,
            json.dumps(billing_run_item_ids),
        ),
    )
    connection.commit()
    return cursor.lastrowid


def list_all(connection: sqlite3.Connection) -> list[MahnungLog]:
    """List every recorded Mahnung send, most recent first.

    Args:
        connection: Open SQLite connection.

    Returns:
        All log entries, ordered by `sent_at` descending.
    """
    rows = connection.execute(
        "SELECT * FROM mahnung_log ORDER BY sent_at DESC, id DESC"
    ).fetchall()
    return [MahnungLog.from_row(row) for row in rows]


def list_for_person(connection: sqlite3.Connection, person_id: int) -> list[MahnungLog]:
    """List every recorded Mahnung send for one person, most recent first.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.

    Returns:
        That person's log entries, ordered by `sent_at` descending.
    """
    rows = connection.execute(
        "SELECT * FROM mahnung_log WHERE person_id = ? ORDER BY sent_at DESC, id DESC",
        (person_id,),
    ).fetchall()
    return [MahnungLog.from_row(row) for row in rows]
