"""Sent-history log for broadcast/LEG emails (see `app.emailing.bulk_send.
send_broadcast_email`).

Unlike invoice emails (tracked per `billing_run_items.email_sent_at`,
see `app.models.billing_run`), a broadcast has no natural row to attach a
"sent" flag to -- this table is that record instead, so an interrupted
batch send stays traceable ("did everyone already get this?") and
Michael has a history of what was announced when.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class EmailBroadcastLog:
    """One completed broadcast/LEG email send.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        sent_at: ISO-8601 timestamp the send completed.
        scope: `"all"` or `"leg"`.
        leg_id: The LEG this was sent to, if `scope == "leg"`, else `None`.
        subject: The (unrendered, with placeholders) subject template used.
        body: The (unrendered, with placeholders) body template used.
        recipient_emails: Email addresses of everyone the send actually
            succeeded for (not everyone it was attempted for) -- the
            honest record of who really got this message.
        attachment_filename: Name of the file attached to this send, or
            `None` if it was sent without an attachment.
    """

    id: Optional[int]
    sent_at: str
    scope: str
    leg_id: Optional[int]
    subject: str
    body: str
    recipient_emails: list[str] = field(default_factory=list)
    attachment_filename: Optional[str] = None

    @property
    def recipient_count(self) -> int:
        """Number of recipients actually reached.

        Returns:
            `len(recipient_emails)`.
        """
        return len(self.recipient_emails)

    @staticmethod
    def from_row(row: sqlite3.Row) -> "EmailBroadcastLog":
        """Build an `EmailBroadcastLog` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `email_broadcast_log` table.

        Returns:
            The corresponding `EmailBroadcastLog` dataclass instance.
        """
        return EmailBroadcastLog(
            id=row["id"],
            sent_at=row["sent_at"],
            scope=row["scope"],
            leg_id=row["leg_id"],
            subject=row["subject"],
            body=row["body"],
            recipient_emails=json.loads(row["recipient_emails"]),
            attachment_filename=row["attachment_filename"],
        )


def create(
    connection: sqlite3.Connection,
    *,
    scope: str,
    leg_id: Optional[int],
    subject: str,
    body: str,
    recipient_emails: list[str],
    attachment_filename: Optional[str] = None,
) -> int:
    """Record one completed broadcast/LEG email send.

    Args:
        connection: Open SQLite connection.
        scope: `"all"` or `"leg"`.
        leg_id: The LEG sent to, if `scope == "leg"`, else `None`.
        subject: The subject template used (with placeholders, unrendered).
        body: The body template used (with placeholders, unrendered).
        recipient_emails: Email addresses actually reached.
        attachment_filename: Name of the file attached to this send, or
            `None` if it was sent without one.

    Returns:
        The primary key of the new log entry.
    """
    cursor = connection.execute(
        """
        INSERT INTO email_broadcast_log
            (sent_at, scope, leg_id, subject, body, recipient_count, recipient_emails, attachment_filename)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            scope,
            leg_id,
            subject,
            body,
            len(recipient_emails),
            json.dumps(recipient_emails),
            attachment_filename,
        ),
    )
    connection.commit()
    return cursor.lastrowid


def list_all(connection: sqlite3.Connection) -> list[EmailBroadcastLog]:
    """List every recorded broadcast/LEG email send, most recent first.

    Args:
        connection: Open SQLite connection.

    Returns:
        All log entries, ordered by `sent_at` descending.
    """
    rows = connection.execute(
        "SELECT * FROM email_broadcast_log ORDER BY sent_at DESC, id DESC"
    ).fetchall()
    return [EmailBroadcastLog.from_row(row) for row in rows]
