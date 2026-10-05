"""SQLite connection helpers."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.paths import DATABASE_PATH, ensure_directories


def create_connection(db_path: Path = DATABASE_PATH) -> sqlite3.Connection:
    """Open a new SQLite connection configured for application use."""
    ensure_directories()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def connection_scope(db_path: Path = DATABASE_PATH) -> Iterator[sqlite3.Connection]:
    """Provide a connection as a context manager that commits or rolls back."""
    connection = create_connection(db_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
