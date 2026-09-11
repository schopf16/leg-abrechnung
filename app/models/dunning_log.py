"""Sent-history log for dunning notices (see `app.domain.dunning`), analogous
to `app.models.email_log.EmailBroadcastLog` for broadcast emails.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class DunningLog:
    """One completed dunning notice send (a single, possibly consolidated letter
    covering one or more open billing run items for one Person).

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        sent_at: ISO-8601 timestamp the send completed.
        person_id: The Person this dunning notice was sent to.
        level: `1` or `2`, the stage reached by this send.
        amount_rappen: The total amount the dunning notice stated as owed, at
            the moment it was sent.
        billing_run_item_ids: The billing run items this dunning notice covered.
    """

    id: int | None
    sent_at: str
    person_id: int
    level: int
    amount_rappen: int
    billing_run_item_ids: list[int]

    @staticmethod
    def from_row(row: sqlite3.Row) -> "DunningLog":
        """Build a `DunningLog` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `dunning_log` table.

        Returns:
            The corresponding `DunningLog` dataclass instance.
        """
        return DunningLog(
            id=row["id"],
            sent_at=row["sent_at"],
            person_id=row["person_id"],
            level=row["level"],
            amount_rappen=row["amount_rappen"],
            billing_run_item_ids=json.loads(row["billing_run_item_ids"]),
        )


def create(
    connection: sqlite3.Connection,
    *,
    person_id: int,
    level: int,
    amount_rappen: int,
    billing_run_item_ids: list[int],
) -> int:
    """Record one completed dunning notice send.

    Args:
        connection: Open SQLite connection.
        person_id: The Person this dunning notice was sent to.
        level: `1` or `2`.
        amount_rappen: The total amount stated as owed.
        billing_run_item_ids: The billing run items covered.

    Returns:
        The primary key of the new log entry.
    """
    cursor = connection.execute(
        """
        INSERT INTO dunning_log (sent_at, person_id, level, amount_rappen, billing_run_item_ids)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            person_id,
            level,
            amount_rappen,
            json.dumps(billing_run_item_ids),
        ),
    )
    connection.commit()
    return cursor.lastrowid


def list_all(connection: sqlite3.Connection) -> list[DunningLog]:
    """List every recorded dunning notice send, most recent first.

    Args:
        connection: Open SQLite connection.

    Returns:
        All log entries, ordered by `sent_at` descending.
    """
    rows = connection.execute("SELECT * FROM dunning_log ORDER BY sent_at DESC, id DESC").fetchall()
    return [DunningLog.from_row(row) for row in rows]


def list_for_person(connection: sqlite3.Connection, person_id: int) -> list[DunningLog]:
    """List every recorded dunning notice send for one person, most recent first.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.

    Returns:
        That person's log entries, ordered by `sent_at` descending.
    """
    rows = connection.execute(
        "SELECT * FROM dunning_log WHERE person_id = ? ORDER BY sent_at DESC, id DESC",
        (person_id,),
    ).fetchall()
    return [DunningLog.from_row(row) for row in rows]
