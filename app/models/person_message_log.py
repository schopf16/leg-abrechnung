"""What has been sent to whom, when, and by which route."""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


#: The message really went out as an email.
CHANNEL_EMAIL = "email"
#: The administrator marked the baustein done without sending anything,
#: because the letter had already been handed over on paper. Such a row
#: carries no text and no recipients, because there was no message.
CHANNEL_MANUAL = "manual"


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
    channel: str = CHANNEL_EMAIL

    @property
    def by_hand(self) -> bool:
        """Whether this was marked done rather than actually sent."""
        return self.channel == CHANNEL_MANUAL

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
            channel=row["channel"],
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
           subject, body, recipient_emails, attachment_filenames, channel
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
    channel: str = CHANNEL_EMAIL,
    commit: bool = True,
) -> int:
    """Record one completed send, or one marked done without sending."""
    cursor = connection.execute(
        """
        INSERT INTO person_message_log
            (sent_at, person_id, template_id, occasion, step,
             subject, body, recipient_emails, attachment_filenames, channel)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            channel,
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


#: The newest row per (person, template). `MAX(id)` rather than
#: `MAX(sent_at)`: marking several bausteine done in one go writes
#: timestamps that can tie, and an id cannot. Written out rather than
#: assembled from `_SELECT`, so nothing here builds SQL from strings.
_SELECT_LATEST = """
    SELECT id, sent_at, person_id, template_id, occasion, step,
           subject, body, recipient_emails, attachment_filenames, channel
    FROM person_message_log
    WHERE id IN (
        SELECT MAX(id) FROM person_message_log
        WHERE template_id IS NOT NULL
        GROUP BY person_id, template_id
    )
"""


def latest_everywhere(connection: sqlite3.Connection) -> dict[tuple[int, int], PersonMessageLog]:
    """The last message per person and template, for a whole worklist.

    One query, because a worklist renders one card per tracker and each card
    asks the same question -- asking per card is how a list of ninety gets
    slow.
    """
    rows = connection.execute(_SELECT_LATEST).fetchall()
    return {(row["person_id"], row["template_id"]): PersonMessageLog.from_row(row) for row in rows}


def latest_by_template(connection: sqlite3.Connection, person_id: int) -> dict[int, PersonMessageLog]:
    """The last message per template for one person."""
    rows = connection.execute(_SELECT_LATEST + " AND person_id = ?", (person_id,)).fetchall()
    return {row["template_id"]: PersonMessageLog.from_row(row) for row in rows}


def delete(connection: sqlite3.Connection, log_id: int, *, commit: bool = True) -> None:
    """Remove one log row, which undoes a marked-done.

    Only ever used for that: a message that really went out is a fact, and
    the card offers no way to unsay it.
    """
    connection.execute("DELETE FROM person_message_log WHERE id = ?", (log_id,))
    if commit:
        connection.commit()
