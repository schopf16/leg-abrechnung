"""The LEG's own forms, kept once and attached by whoever ticks them."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class LegDocument:
    """One stored LEG form."""

    key: str
    filename: str
    content: bytes
    updated_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "LegDocument":
        """Build a `LegDocument` from a `sqlite3.Row`."""
        return LegDocument(
            key=row["key"],
            filename=row["filename"],
            content=row["content"],
            updated_at=row["updated_at"],
        )


def get(connection: sqlite3.Connection, key: str) -> Optional[LegDocument]:
    """Load one stored form."""
    row = connection.execute(
        "SELECT key, filename, content, updated_at FROM leg_document WHERE key = ?",
        (key,),
    ).fetchone()
    return LegDocument.from_row(row) if row else None


def stored_keys(connection: sqlite3.Connection) -> set[str]:
    """Which forms have a file."""
    return {row[0] for row in connection.execute("SELECT key FROM leg_document")}


def list_all(connection: sqlite3.Connection) -> list[LegDocument]:
    """Every stored form."""
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
    """Store or replace one form."""
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
    """Remove one stored form."""
    connection.execute("DELETE FROM leg_document WHERE key = ?", (key,))
    if commit:
        connection.commit()
