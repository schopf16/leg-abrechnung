"""The LEG's own forms, kept once and attached by whoever ticks them.

The blank Gesellschaftsvertrag is a LEG-wide document: seven pages that
every new participant gets, and page 1 of it is what
`app.pdf.membership_contract` fills in. It belongs to the LEG, not to one
email text, so it is uploaded once under Einstellungen and any Textbaustein
can tick it (`app.domain.auto_attachments`).

Keyed by the registry key rather than by an id, because there is exactly
one current version of each form -- uploading replaces it, which is how a
new Reglement edition is taken on. **In the database rather than beside
it**, for two reasons: it travels with every backup (which a file under
`data/` does not -- see CLAUDE.md on the address register, which is a file
*because* it can be re-downloaded), and a contract's version has to stay
knowable after it has been sent.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class LegDocument:
    """One stored LEG form.

    Attributes:
        key: The `app.domain.auto_attachments` key this is the form for.
        filename: Name the recipient sees.
        content: The file's bytes.
        updated_at: ISO-8601 timestamp of the last upload.
    """

    key: str
    filename: str
    content: bytes
    updated_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "LegDocument":
        """Build a `LegDocument` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `leg_document` table.

        Returns:
            The corresponding dataclass instance.
        """
        return LegDocument(
            key=row["key"],
            filename=row["filename"],
            content=row["content"],
            updated_at=row["updated_at"],
        )


def get(connection: sqlite3.Connection, key: str) -> Optional[LegDocument]:
    """Load one stored form.

    Args:
        connection: Open SQLite connection.
        key: The registry key.

    Returns:
        The document, or `None` if none has been uploaded yet.
    """
    row = connection.execute(
        "SELECT key, filename, content, updated_at FROM leg_document WHERE key = ?",
        (key,),
    ).fetchone()
    return LegDocument.from_row(row) if row else None


def stored_keys(connection: sqlite3.Connection) -> set[str]:
    """Which forms have a file.

    One query for the whole dialog, which needs to know this for every
    checkbox it draws.

    Args:
        connection: Open SQLite connection.

    Returns:
        The keys that have a document.
    """
    return {row[0] for row in connection.execute("SELECT key FROM leg_document")}


def list_all(connection: sqlite3.Connection) -> list[LegDocument]:
    """Every stored form.

    Args:
        connection: Open SQLite connection.

    Returns:
        The documents, by key.
    """
    rows = connection.execute(
        "SELECT key, filename, content, updated_at FROM leg_document ORDER BY key"
    ).fetchall()
    return [LegDocument.from_row(row) for row in rows]


def put(
    connection: sqlite3.Connection,
    key: str,
    filename: str,
    content: bytes,
    *,
    commit: bool = True,
) -> None:
    """Store or replace one form.

    Replacing is how a new edition of the Reglement is taken on, so this is
    deliberately an upsert rather than refusing a second upload.

    Args:
        connection: Open SQLite connection.
        key: The registry key.
        filename: Name the recipient sees.
        content: The file's bytes.
        commit: Pass `False` when an enclosing `connection_scope` commits.

    Returns:
        None.
    """
    connection.execute(
        """
        INSERT INTO leg_document (key, filename, content, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            filename = excluded.filename,
            content = excluded.content,
            updated_at = excluded.updated_at
        """,
        (key, filename, content, datetime.now(timezone.utc).isoformat()),
    )
    if commit:
        connection.commit()


def delete(connection: sqlite3.Connection, key: str, *, commit: bool = True) -> None:
    """Remove one stored form.

    A template that ticks it keeps the tick: the dialog then says the form
    is missing, which is the honest state rather than silently unticking
    something the administrator chose.

    Args:
        connection: Open SQLite connection.
        key: The registry key.
        commit: Pass `False` when an enclosing `connection_scope` commits.

    Returns:
        None.
    """
    connection.execute("DELETE FROM leg_document WHERE key = ?", (key,))
    if commit:
        connection.commit()
