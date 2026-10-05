"""Named, reusable email signatures."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Signature:
    """One named, reusable signature text block."""

    id: Optional[int]
    name: str
    content: str
    created_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Signature":
        """Build a `Signature` from a `sqlite3.Row`."""
        return Signature(
            id=row["id"],
            name=row["name"],
            content=row["content"],
            created_at=row["created_at"],
        )


def list_all(connection: sqlite3.Connection) -> list[Signature]:
    """List all signatures, ordered by name."""
    rows = connection.execute("SELECT * FROM signatures ORDER BY name").fetchall()
    return [Signature.from_row(row) for row in rows]


def get(connection: sqlite3.Connection, signature_id: int) -> Optional[Signature]:
    """Fetch a single signature by id."""
    row = connection.execute("SELECT * FROM signatures WHERE id = ?", (signature_id,)).fetchone()
    return Signature.from_row(row) if row else None


def get_by_name(connection: sqlite3.Connection, name: str) -> Optional[Signature]:
    """Fetch a single signature by its exact name."""
    row = connection.execute("SELECT * FROM signatures WHERE name = ?", (name,)).fetchone()
    return Signature.from_row(row) if row else None


def create(connection: sqlite3.Connection, signature: Signature) -> int:
    """Insert a new signature."""
    cursor = connection.execute(
        """
        INSERT INTO signatures (name, content, created_at)
        VALUES (?, ?, ?)
        """,
        (
            signature.name,
            signature.content,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, signature: Signature) -> None:
    """Update an existing signature's name/content."""
    if signature.id is None:
        raise ValueError("Cannot update a Signature without an id.")
    connection.execute(
        "UPDATE signatures SET name = ?, content = ? WHERE id = ?",
        (signature.name, signature.content, signature.id),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, signature_id: int) -> None:
    """Delete a signature."""
    connection.execute("DELETE FROM signatures WHERE id = ?", (signature_id,))
    connection.commit()
