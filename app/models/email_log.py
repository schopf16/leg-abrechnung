"""Sent-history log for broadcast/LEG emails (see `app.emailing.bulk_send. send_broadcast_email`)."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class EmailBroadcastLog:
    """One completed broadcast/LEG email send."""

    id: Optional[int]
    sent_at: str
    scope: str
    leg_id: Optional[int]
    subject: str
    body: str
    recipient_emails: list[str] = field(default_factory=list)
    attachment_filenames: Optional[str] = None

    @property
    def recipient_count(self) -> int:
        """Number of recipients actually reached."""
        return len(self.recipient_emails)

    @staticmethod
    def from_row(row: sqlite3.Row) -> "EmailBroadcastLog":
        """Build an `EmailBroadcastLog` from a `sqlite3.Row`."""
        return EmailBroadcastLog(
            id=row["id"],
            sent_at=row["sent_at"],
            scope=row["scope"],
            leg_id=row["leg_id"],
            subject=row["subject"],
            body=row["body"],
            recipient_emails=json.loads(row["recipient_emails"]),
            attachment_filenames=row["attachment_filenames"],
        )


def create(
    connection: sqlite3.Connection,
    *,
    scope: str,
    leg_id: Optional[int],
    subject: str,
    body: str,
    recipient_emails: list[str],
    attachment_filenames: Optional[str] = None,
) -> int:
    """Record one completed broadcast/LEG email send."""
    cursor = connection.execute(
        """
        INSERT INTO email_broadcast_log
            (sent_at, scope, leg_id, subject, body, recipient_count, recipient_emails, attachment_filenames)
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
            attachment_filenames,
        ),
    )
    connection.commit()
    return cursor.lastrowid


def list_all(connection: sqlite3.Connection) -> list[EmailBroadcastLog]:
    """List every recorded broadcast/LEG email send, most recent first."""
    rows = connection.execute("SELECT * FROM email_broadcast_log ORDER BY sent_at DESC, id DESC").fetchall()
    return [EmailBroadcastLog.from_row(row) for row in rows]
