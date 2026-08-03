"""SQLite connection helpers.

The whole application talks to a single local SQLite file. Connections are
opened with foreign key enforcement turned on and a row factory that returns
dict-like rows, so callers can use ``row["column"]`` instead of positional
indices.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.paths import DATABASE_PATH, ensure_directories


def create_connection(db_path: Path = DATABASE_PATH) -> sqlite3.Connection:
    """Open a new SQLite connection configured for application use.

    Args:
        db_path: Filesystem path of the SQLite database file. Parent
            directories are created if they do not exist yet.

    Returns:
        A ``sqlite3.Connection`` with ``row_factory`` set to
        ``sqlite3.Row`` and foreign key constraints enabled.
    """
    ensure_directories()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def connection_scope(db_path: Path = DATABASE_PATH) -> Iterator[sqlite3.Connection]:
    """Provide a connection as a context manager that commits or rolls back.

    Args:
        db_path: Filesystem path of the SQLite database file.

    Yields:
        An open ``sqlite3.Connection``. On successful exit the transaction
        is committed; on exception it is rolled back and the exception is
        re-raised.
    """
    connection = create_connection(db_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
