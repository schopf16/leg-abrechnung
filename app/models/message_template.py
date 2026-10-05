"""Textbausteine: the stored email texts, and the files that go with them."""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

#: Occasions a template can belong to. English values, as every persisted
#: enum has been since migration 43.
OCCASION_ONBOARDING = "onboarding"
OCCASION_OFFBOARDING = "offboarding"
OCCASION_INVOICE = "invoice"
OCCASION_DUNNING1 = "dunning1"
OCCASION_DUNNING2 = "dunning2"

#: German labels, in the order the list shows them.
OCCASION_LABELS = {
    OCCASION_ONBOARDING: "Aufnahme",
    OCCASION_OFFBOARDING: "Austritt",
    OCCASION_INVOICE: "Rechnung",
    OCCASION_DUNNING1: "1. Mahnung",
    OCCASION_DUNNING2: "2. Mahnung",
}

#: The two occasions that hang off a process step.
STEP_OCCASIONS = (OCCASION_ONBOARDING, OCCASION_OFFBOARDING)

#: Due once the step has a date.
TRIGGER_STEP_DONE = "step_done"
#: Due while the step has none.
TRIGGER_STEP_PENDING = "step_pending"

TRIGGER_LABELS = {
    TRIGGER_STEP_DONE: "wenn Schritt erledigt",
    TRIGGER_STEP_PENDING: "solange Schritt offen",
    "": "ohne Schritt",
}

#: `message_template_attachment.role` is **not read any more**. Migration 52
#: put it there to mark the membership contract, and the dialog had to ask
#: "neue Anhänge behandeln als" per upload -- a question the administrator
#: could not make sense of, because it was the wrong one: not what kind of
#: file this is, but which of our documents should go along. That is
#: `auto_attachments` below and `app.domain.auto_attachments`. The column
#: stays, unread, like `leg_settings.leg_founding_min_persons` -- old
#: migrations are never rewritten.


@dataclass
class MessageTemplate:
    """One stored email text."""

    id: Optional[int]
    name: str
    occasion: str
    step: str
    trigger_kind: str
    deadline_days: Optional[int]
    subject: str
    body: str
    sort_order: int
    created_at: str
    auto_attachments: list[str] = field(default_factory=list)

    @staticmethod
    def from_row(row: sqlite3.Row) -> "MessageTemplate":
        """Build a `MessageTemplate` from a `sqlite3.Row`."""
        return MessageTemplate(
            id=row["id"],
            name=row["name"],
            occasion=row["occasion"],
            step=row["step"],
            trigger_kind=row["trigger_kind"],
            deadline_days=row["deadline_days"],
            subject=row["subject"],
            body=row["body"],
            sort_order=row["sort_order"],
            created_at=row["created_at"],
            auto_attachments=[key for key in (row["auto_attachments"] or "").splitlines() if key],
        )

    @property
    def occasion_label(self) -> str:
        """The occasion in German, as the list shows it."""
        return OCCASION_LABELS.get(self.occasion, self.occasion)


@dataclass
class TemplateAttachment:
    """One file belonging to a template."""

    id: Optional[int]
    template_id: int
    filename: str
    content: bytes
    role: str
    created_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "TemplateAttachment":
        """Build a `TemplateAttachment` from a `sqlite3.Row`."""
        return TemplateAttachment(
            id=row["id"],
            template_id=row["template_id"],
            filename=row["filename"],
            content=row["content"],
            role=row["role"],
            created_at=row["created_at"],
        )


#: How the ticked document keys are stored in one column. A newline for the
#: reason `app.models.email_log` gives about filenames: it cannot occur
#: inside a value, which a comma could.
_KEY_SEPARATOR = "\n"

_SELECT = """
    SELECT id, name, occasion, step, trigger_kind, deadline_days,
           subject, body, sort_order, created_at, auto_attachments
    FROM message_template
"""


def list_all(connection: sqlite3.Connection) -> list[MessageTemplate]:
    """List every template."""
    rows = connection.execute(_SELECT + " ORDER BY sort_order, name").fetchall()
    return [MessageTemplate.from_row(row) for row in rows]


def get(connection: sqlite3.Connection, template_id: int) -> Optional[MessageTemplate]:
    """Load one template."""
    row = connection.execute(_SELECT + " WHERE id = ?", (template_id,)).fetchone()
    return MessageTemplate.from_row(row) if row else None


def list_for_occasion(connection: sqlite3.Connection, occasion: str, step: str = "") -> list[MessageTemplate]:
    """Templates for one occasion, optionally narrowed to one step."""
    if step:
        rows = connection.execute(
            _SELECT + " WHERE occasion = ? AND step = ? ORDER BY sort_order, name",
            (occasion, step),
        ).fetchall()
    else:
        rows = connection.execute(
            _SELECT + " WHERE occasion = ? ORDER BY sort_order, name", (occasion,)
        ).fetchall()
    return [MessageTemplate.from_row(row) for row in rows]


def create(connection: sqlite3.Connection, template: MessageTemplate, *, commit: bool = True) -> int:
    """Insert a template."""
    cursor = connection.execute(
        """
        INSERT INTO message_template
            (name, occasion, step, trigger_kind, deadline_days,
             subject, body, sort_order, created_at, auto_attachments)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            template.name,
            template.occasion,
            template.step,
            template.trigger_kind,
            template.deadline_days,
            template.subject,
            template.body,
            template.sort_order,
            datetime.now(timezone.utc).isoformat(),
            _KEY_SEPARATOR.join(template.auto_attachments),
        ),
    )
    if commit:
        connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, template: MessageTemplate, *, commit: bool = True) -> None:
    """Update a template in place."""
    connection.execute(
        """
        UPDATE message_template
        SET name = ?, occasion = ?, step = ?, trigger_kind = ?,
            deadline_days = ?, subject = ?, body = ?, sort_order = ?,
            auto_attachments = ?
        WHERE id = ?
        """,
        (
            template.name,
            template.occasion,
            template.step,
            template.trigger_kind,
            template.deadline_days,
            template.subject,
            template.body,
            template.sort_order,
            _KEY_SEPARATOR.join(template.auto_attachments),
            template.id,
        ),
    )
    if commit:
        connection.commit()


def delete(connection: sqlite3.Connection, template_id: int, *, commit: bool = True) -> None:
    """Delete a template and its attachments."""
    connection.execute("DELETE FROM message_template WHERE id = ?", (template_id,))
    if commit:
        connection.commit()


# -- attachments ------------------------------------------------------------


def list_attachments(connection: sqlite3.Connection, template_id: int) -> list[TemplateAttachment]:
    """The files belonging to one template."""
    rows = connection.execute(
        """
        SELECT id, template_id, filename, content, role, created_at
        FROM message_template_attachment
        WHERE template_id = ?
        ORDER BY id
        """,
        (template_id,),
    ).fetchall()
    return [TemplateAttachment.from_row(row) for row in rows]


def add_attachment(
    connection: sqlite3.Connection,
    template_id: int,
    filename: str,
    content: bytes,
    *,
    role: str = "",
    commit: bool = True,
) -> int:
    """Attach a file to a template."""
    cursor = connection.execute(
        """
        INSERT INTO message_template_attachment
            (template_id, filename, content, role, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (template_id, filename, content, role, datetime.now(timezone.utc).isoformat()),
    )
    if commit:
        connection.commit()
    return cursor.lastrowid


def delete_attachment(connection: sqlite3.Connection, attachment_id: int, *, commit: bool = True) -> None:
    """Remove one attachment."""
    connection.execute("DELETE FROM message_template_attachment WHERE id = ?", (attachment_id,))
    if commit:
        connection.commit()
