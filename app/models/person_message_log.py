"""What has been sent to whom, and when."""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class PersonMessageLog:
    """One completed send to one person."""

    id: Optional[int]
    sent_at: str
    person_id: int
    template_id: Optional[int]
    occasion: str
    step: str
    subject: str
    body: str
    recipient_emails: list[str] = field(default_factory=list)
    attachment_filenames: list[str] = field(default_factory=list)

    @staticmethod
    def from_row(row: sqlite3.Row) -> "PersonMessageLog":
        """Build a `PersonMessageLog` from a `sqlite3.Row`."""
        return PersonMessageLog(
            id=row["id"],
            sent_at=row["sent_at"],
            person_id=row["person_id"],
            template_id=row["template_id"],
            occasion=row["occasion"],
            step=row["step"],
            subject=row["subject"],
            body=row["body"],
            recipient_emails=_lines(row["recipient_emails"]),
            attachment_filenames=_lines(row["attachment_filenames"]),
        )

    @property
    def sent_on(self) -> str:
        """The send date alone, for the button that became a date."""
        return self.sent_at[:10]


def _lines(value: Optional[str]) -> list[str]:
    """Split a newline-separated column into a list."""
    return [line for line in (value or "").splitlines() if line]


_SELECT = """
    SELECT id, sent_at, person_id, template_id, occasion, step,
           subject, body, recipient_emails, attachment_filenames
    FROM person_message_log
"""


def record(
    connection: sqlite3.Connection,
    *,
    person_id: int,
    template_id: Optional[int],
    occasion: str,
    step: str,
    subject: str,
    body: str,
    recipient_emails: list[str],
    attachment_filenames: list[str],
    commit: bool = True,
) -> int:
    """Record one completed send."""
    cursor = connection.execute(
        """
        INSERT INTO person_message_log
            (sent_at, person_id, template_id, occasion, step,
             subject, body, recipient_emails, attachment_filenames)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            person_id,
            template_id,
            occasion,
            step,
            subject,
            body,
            "\n".join(recipient_emails),
            "\n".join(attachment_filenames),
        ),
    )
    if commit:
        connection.commit()
    return cursor.lastrowid


def list_for_person(connection: sqlite3.Connection, person_id: int) -> list[PersonMessageLog]:
    """Everything sent to one person, newest first."""
    rows = connection.execute(_SELECT + " WHERE person_id = ? ORDER BY sent_at DESC", (person_id,)).fetchall()
    return [PersonMessageLog.from_row(row) for row in rows]


def last_sent(connection: sqlite3.Connection, person_id: int, template_id: int) -> Optional[PersonMessageLog]:
    """The most recent send of one template to one person."""
    row = connection.execute(
        _SELECT + " WHERE person_id = ? AND template_id = ? ORDER BY sent_at DESC LIMIT 1",
        (person_id, template_id),
    ).fetchone()
    return PersonMessageLog.from_row(row) if row else None


def sent_dates_by_template(connection: sqlite3.Connection, person_id: int) -> dict[int, str]:
    """When each template was last sent to one person."""
    rows = connection.execute(
        """
        SELECT template_id, MAX(sent_at) AS sent_at
        FROM person_message_log
        WHERE person_id = ? AND template_id IS NOT NULL
        GROUP BY template_id
        """,
        (person_id,),
    ).fetchall()
    return {row["template_id"]: row["sent_at"] for row in rows}
