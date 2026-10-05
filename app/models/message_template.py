"""Textbausteine: the stored email texts, and the files that go with them.

Every email this app sends has a text behind it, and until migration 52
those texts lived in three places with three shapes: column pairs on
`leg_settings` for the invoice and the two dunning notices, nothing at all
for the broadcast (it was retyped every time), and no provision whatever for
the Aufnahme and Austritt processes. One table instead, maintained on its
own page under "Kommunikation" (`app.gui.pages.message_templates`).

**`trigger_kind` is the whole vocabulary**, and it is deliberately two
words:

- `TRIGGER_STEP_DONE` -- due once the step carries a date. The mail reports
  something that has happened: "you have been registered with BKW".
- `TRIGGER_STEP_PENDING` -- due while the step is still empty, which is
  where `deadline_days` belongs: a contract that has not come back after 30
  days.

Nothing here sends anything. A trigger decides when a **button** appears,
never when a mail leaves -- see `app.domain.message_templates`.

**Several templates per step is the point, not an accident.** A reminder is
a second text about the same step, so template-to-step is many-to-one.

An attachment's `role` is why the filled-in membership contract is declared
in the data rather than recognised by its filename: that one is not attached
but **generated** -- page 1 from the person's own record, the rest from the
stored original (see `app.pdf.membership_contract`). The bytes live in this
database rather than in a file beside it, so they travel with every backup
and so it is always knowable which version of a contract was sent.
"""

import sqlite3
from dataclasses import dataclass
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

#: An attachment that is generated rather than sent as stored: page 1 is
#: filled from the person's record, the remaining pages come from this file.
ROLE_MEMBERSHIP_CONTRACT = "membership_contract"

ROLE_LABELS = {
    "": "unverändert anhängen",
    ROLE_MEMBERSHIP_CONTRACT: "Seite 1 ausfüllen, Rest anhängen",
}


@dataclass
class MessageTemplate:
    """One stored email text.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        name: What the administrator calls it ("Willkommen").
        occasion: One of the `OCCASION_*` constants.
        step: The `STEPS` attribute name it belongs to (e.g.
            `"contract_signed_at"`), or `""` for an occasion without steps.
        trigger_kind: One of the `TRIGGER_*` constants, or `""`.
        deadline_days: Days the step may stay open before the template
            becomes due. Only meaningful with `TRIGGER_STEP_PENDING`;
            `None` means "due at once".
        subject: Subject, may contain `{placeholder}`s.
        body: Body, same.
        sort_order: Position in the list; ties fall back to the name.
        created_at: ISO-8601 creation timestamp.
    """

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

    @staticmethod
    def from_row(row: sqlite3.Row) -> "MessageTemplate":
        """Build a `MessageTemplate` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `message_template` table.

        Returns:
            The corresponding dataclass instance.
        """
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
        )

    @property
    def occasion_label(self) -> str:
        """The occasion in German, as the list shows it.

        Returns:
            e.g. `"Aufnahme"`, or the raw value if it is unknown.
        """
        return OCCASION_LABELS.get(self.occasion, self.occasion)


@dataclass
class TemplateAttachment:
    """One file belonging to a template.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        template_id: The template this belongs to.
        filename: Name the recipient sees.
        content: The file's bytes.
        role: `""` to attach as stored, or `ROLE_MEMBERSHIP_CONTRACT`.
        created_at: ISO-8601 creation timestamp.
    """

    id: Optional[int]
    template_id: int
    filename: str
    content: bytes
    role: str
    created_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "TemplateAttachment":
        """Build a `TemplateAttachment` from a `sqlite3.Row`.

        Args:
            row: Row selected from `message_template_attachment`.

        Returns:
            The corresponding dataclass instance.
        """
        return TemplateAttachment(
            id=row["id"],
            template_id=row["template_id"],
            filename=row["filename"],
            content=row["content"],
            role=row["role"],
            created_at=row["created_at"],
        )

    @property
    def is_generated(self) -> bool:
        """Whether this file is built per person rather than sent as stored.

        Returns:
            `True` for the membership contract.
        """
        return self.role == ROLE_MEMBERSHIP_CONTRACT


_SELECT = """
    SELECT id, name, occasion, step, trigger_kind, deadline_days,
           subject, body, sort_order, created_at
    FROM message_template
"""


def list_all(connection: sqlite3.Connection) -> list[MessageTemplate]:
    """List every template.

    Ordered in SQL only by `sort_order` and name, both plain ASCII the
    administrator controls; the browsable list sorts in Python like every
    other list (see `app/gui/sorting.py` on why `ORDER BY` is not trusted
    with names).

    Args:
        connection: Open SQLite connection.

    Returns:
        The templates.
    """
    rows = connection.execute(_SELECT + " ORDER BY sort_order, name").fetchall()
    return [MessageTemplate.from_row(row) for row in rows]


def get(connection: sqlite3.Connection, template_id: int) -> Optional[MessageTemplate]:
    """Load one template.

    Args:
        connection: Open SQLite connection.
        template_id: Primary key.

    Returns:
        The template, or `None` if there is no such row.
    """
    row = connection.execute(_SELECT + " WHERE id = ?", (template_id,)).fetchone()
    return MessageTemplate.from_row(row) if row else None


def list_for_occasion(connection: sqlite3.Connection, occasion: str, step: str = "") -> list[MessageTemplate]:
    """Templates for one occasion, optionally narrowed to one step.

    Args:
        connection: Open SQLite connection.
        occasion: One of the `OCCASION_*` constants.
        step: A `STEPS` attribute name, or `""` for every step.

    Returns:
        The matching templates, in list order.
    """
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
    """Insert a template.

    Args:
        connection: Open SQLite connection.
        template: Data to insert; `id` and `created_at` are ignored.
        commit: Pass `False` when an enclosing `connection_scope` commits.

    Returns:
        The new row's id.
    """
    cursor = connection.execute(
        """
        INSERT INTO message_template
            (name, occasion, step, trigger_kind, deadline_days,
             subject, body, sort_order, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    if commit:
        connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, template: MessageTemplate, *, commit: bool = True) -> None:
    """Update a template in place.

    Args:
        connection: Open SQLite connection.
        template: The template, with `id` set.
        commit: Pass `False` when an enclosing `connection_scope` commits.

    Returns:
        None.
    """
    connection.execute(
        """
        UPDATE message_template
        SET name = ?, occasion = ?, step = ?, trigger_kind = ?,
            deadline_days = ?, subject = ?, body = ?, sort_order = ?
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
            template.id,
        ),
    )
    if commit:
        connection.commit()


def delete(connection: sqlite3.Connection, template_id: int, *, commit: bool = True) -> None:
    """Delete a template and its attachments.

    The attachments go with it (`ON DELETE CASCADE`), and a past send keeps
    its own copy of the text it used (`app.models.person_message_log`), so
    deleting is always safe -- nothing reads back through here.

    Args:
        connection: Open SQLite connection.
        template_id: Primary key.
        commit: Pass `False` when an enclosing `connection_scope` commits.

    Returns:
        None.
    """
    connection.execute("DELETE FROM message_template WHERE id = ?", (template_id,))
    if commit:
        connection.commit()


# -- attachments ------------------------------------------------------------


def list_attachments(connection: sqlite3.Connection, template_id: int) -> list[TemplateAttachment]:
    """The files belonging to one template.

    Args:
        connection: Open SQLite connection.
        template_id: The template.

    Returns:
        Its attachments, oldest first.
    """
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
    """Attach a file to a template.

    Args:
        connection: Open SQLite connection.
        template_id: The template.
        filename: Name the recipient sees.
        content: The file's bytes.
        role: `""` or `ROLE_MEMBERSHIP_CONTRACT`.
        commit: Pass `False` when an enclosing `connection_scope` commits.

    Returns:
        The new row's id.
    """
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
    """Remove one attachment.

    Args:
        connection: Open SQLite connection.
        attachment_id: Primary key.
        commit: Pass `False` when an enclosing `connection_scope` commits.

    Returns:
        None.
    """
    connection.execute("DELETE FROM message_template_attachment WHERE id = ?", (attachment_id,))
    if commit:
        connection.commit()
