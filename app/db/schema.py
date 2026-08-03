"""Schema versioning and migration runner.

The database carries its schema version in the ``schema_meta`` table. On
every application start (and before restoring a backup) :func:`migrate_to_latest`
is called: it applies every migration in :data:`app.db.migrations.MIGRATIONS`
whose version is higher than the currently stored one, in ascending order,
each inside its own transaction. This is what allows an old backup file to
be opened by a newer version of the application without manual steps.
"""

import logging
import sqlite3

from app.db.migrations import MIGRATIONS

logger = logging.getLogger(__name__)

#: Highest schema version known to this build of the application.
CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version if MIGRATIONS else 0

_META_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
"""


def _ensure_meta_table(connection: sqlite3.Connection) -> None:
    """Create the ``schema_meta`` bookkeeping table if it is missing.

    Args:
        connection: Open SQLite connection.

    Returns:
        None.
    """
    connection.execute(_META_TABLE_SQL)
    connection.commit()


def get_schema_version(connection: sqlite3.Connection) -> int:
    """Read the schema version currently stored in the database.

    Args:
        connection: Open SQLite connection.

    Returns:
        The stored schema version, or ``0`` for a brand-new, empty database.
    """
    _ensure_meta_table(connection)
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row["value"]) if row else 0


def _set_schema_version(connection: sqlite3.Connection, version: int) -> None:
    """Persist the schema version after a successful migration.

    Args:
        connection: Open SQLite connection.
        version: New schema version to store.

    Returns:
        None.
    """
    connection.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


def migrate_to_latest(connection: sqlite3.Connection) -> int:
    """Apply all pending migrations to bring the database up to date.

    Safe to call on every application start: if the database is already at
    :data:`CURRENT_SCHEMA_VERSION`, this is a no-op.

    Args:
        connection: Open SQLite connection to migrate in place.

    Returns:
        The schema version the database is at after migrating.
    """
    current_version = get_schema_version(connection)
    pending = [m for m in MIGRATIONS if m.version > current_version]
    pending.sort(key=lambda m: m.version)

    for migration in pending:
        logger.info(
            "Applying migration %s: %s", migration.version, migration.description
        )
        connection.executescript(migration.sql)
        _set_schema_version(connection, migration.version)
        connection.commit()
        current_version = migration.version

    return current_version


def initialize_database(connection: sqlite3.Connection) -> int:
    """Ensure a database connection is ready for use by the application.

    Creates the bookkeeping table if needed and migrates the schema to the
    latest known version. Also seeds the single ``leg_settings`` row if it
    does not exist yet.

    Args:
        connection: Open SQLite connection.

    Returns:
        The schema version the database is at after initialization.
    """
    version = migrate_to_latest(connection)
    _seed_default_settings(connection)
    return version


def _seed_default_settings(connection: sqlite3.Connection) -> None:
    """Insert the single default LEG settings row if it does not exist.

    The default internal price is 12 Rp./kWh as specified by the project
    brief; the administrator can change it freely afterwards.

    Args:
        connection: Open SQLite connection.

    Returns:
        None.
    """
    from datetime import datetime, timezone

    exists = connection.execute("SELECT 1 FROM leg_settings WHERE id = 1").fetchone()
    if not exists:
        connection.execute(
            "INSERT INTO leg_settings (id, price_rp_per_kwh, updated_at) "
            "VALUES (1, 12.0, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        connection.commit()
