"""What has been sent to whom, and when.

`app.models.dunning_log`'s shape applied to the Textbausteine: one row per
completed send, carrying the text that actually went out rather than a
reference to the template it came from. A template edited afterwards must
not change what the record says was sent, and deleting one must not erase
the fact that something was.

This table is also what the interface reads, which is the whole reason it
exists in this form. The administrator's requirement was: *"ich möchte
verhindern dass die mails zweimal rausgehen. ich möchte einen button haben
zum starten, nach erfolgreichem senden möchte ich dann das datum sehen
damit ich weiss dieser person habe ich das schon einmal gesendet."* So a
step shows a button while there is no row here, and the date once there is
-- the visible state is the safeguard, and a second send has to be asked
for deliberately.

Written **after** a successful send, never before: a row here means a
message left the building.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class PersonMessageLog:
    """One completed send to one person.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        sent_at: ISO-8601 timestamp the send completed.
        person_id: Who it went to.
        template_id: The template used, or `None` once that template has
            been deleted -- the text below stays either way.
        occasion: The template's occasion at send time.
        step: The step it belonged to, or `""`.
        subject: The **rendered** subject, placeholders substituted.
        body: The rendered body.
        recipient_emails: The addresses it actually went to, one per line.
            A couple is one party with two addresses in one message (see
            CLAUDE.md on `app/emailing/`), so there can be two.
        attachment_filenames: Names of the attached files, one per line,
            empty when there were none. A newline rather than a comma for
            the reason `app.models.email_log` gives: a filename may contain
            a comma and cannot contain a newline on Windows.
    """

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
        """Build a `PersonMessageLog` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `person_message_log` table.

        Returns:
            The corresponding dataclass instance.
        """
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
        """The send date alone, for the button that became a date.

        Returns:
            `"YYYY-MM-DD"`, or the raw timestamp if it is not one.
        """
        return self.sent_at[:10]


def _lines(value: Optional[str]) -> list[str]:
    """Split a newline-separated column into a list.

    Args:
        value: The stored text, possibly empty or `None`.

    Returns:
        The non-empty lines.
    """
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
    """Record one completed send.

    Args:
        connection: Open SQLite connection.
        person_id: Who it went to.
        template_id: The template used, if it still exists.
        occasion: The template's occasion.
        step: The step, or `""`.
        subject: The rendered subject.
        body: The rendered body.
        recipient_emails: Addresses the send succeeded for.
        attachment_filenames: Names of the attached files.
        commit: Pass `False` when an enclosing `connection_scope` commits.

    Returns:
        The new row's id.
    """
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
    """Everything sent to one person, newest first.

    Args:
        connection: Open SQLite connection.
        person_id: The person.

    Returns:
        Their send history.
    """
    rows = connection.execute(_SELECT + " WHERE person_id = ? ORDER BY sent_at DESC", (person_id,)).fetchall()
    return [PersonMessageLog.from_row(row) for row in rows]


def last_sent(connection: sqlite3.Connection, person_id: int, template_id: int) -> Optional[PersonMessageLog]:
    """The most recent send of one template to one person.

    This is what turns the button into a date.

    Args:
        connection: Open SQLite connection.
        person_id: The person.
        template_id: The template.

    Returns:
        The newest matching row, or `None` if it has never been sent.
    """
    row = connection.execute(
        _SELECT + " WHERE person_id = ? AND template_id = ? ORDER BY sent_at DESC LIMIT 1",
        (person_id, template_id),
    ).fetchone()
    return PersonMessageLog.from_row(row) if row else None


def sent_dates_by_template(connection: sqlite3.Connection, person_id: int) -> dict[int, str]:
    """When each template was last sent to one person.

    One query for a whole card, rather than one per template: the Aufnahmen
    list draws up to a handful of these per person and ninety-odd persons
    per page.

    Args:
        connection: Open SQLite connection.
        person_id: The person.

    Returns:
        `{template_id: latest sent_at}`, holding only what has been sent.
    """
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
