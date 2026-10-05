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
    row = connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
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
        logger.info("Applying migration %s: %s", migration.version, migration.description)
        connection.executescript(migration.sql)
        _set_schema_version(connection, migration.version)
        connection.commit()
        current_version = migration.version

    return current_version


def initialize_database(connection: sqlite3.Connection) -> int:
    """Ensure a database connection is ready for use by the application.

    Creates the bookkeeping table if needed and migrates the schema to the
    latest known version. Also seeds the single ``leg_settings`` row if it
    does not exist yet, and carries the three email texts that used to live
    on it into ``message_template`` (see ``_seed_message_templates``).

    Args:
        connection: Open SQLite connection.

    Returns:
        The schema version the database is at after initialization.
    """
    version = migrate_to_latest(connection)
    _seed_default_settings(connection)
    _seed_message_templates(connection)
    _split_sender_house_number(connection)
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
            "INSERT INTO leg_settings (id, price_rp_per_kwh, updated_at) VALUES (1, 12.0, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        connection.commit()


#: The three texts that used to be column pairs on `leg_settings`, with the
#: `occasion` each becomes and the column they come from. Order is the order
#: they appear in the Textbausteine list.
_CARRIED_OVER_TEMPLATES = (
    ("Rechnung", "invoice", "invoice_email_subject", "invoice_email_body", 10),
    ("1. Mahnung", "dunning1", "dunning1_email_subject", "dunning1_email_body", 20),
    ("2. Mahnung", "dunning2", "dunning2_email_subject", "dunning2_email_body", 30),
)


def _seed_message_templates(connection: sqlite3.Connection) -> None:
    """Carry the invoice and dunning texts into `message_template`.

    Not done in migration 52, and the reason is worth keeping: on a fresh
    database the `leg_settings` row does not exist while migrations run --
    `_seed_default_settings` above inserts it afterwards -- so an
    `INSERT .. SELECT FROM leg_settings` inside the migration copied nothing
    and a new installation ended up with no invoice text at all. Here, after
    both the migration and the settings row, one code path serves the
    existing database (which has the administrator's real texts) and a fresh
    one (which has the column defaults).

    Runs only while the table is empty, so it cannot overwrite a text that
    has since been edited, and it does not come back after a template is
    deliberately deleted... except that an empty table is indistinguishable
    from "deleted them all", which is a state nobody reaches by accident.

    Args:
        connection: Open SQLite connection, already migrated.

    Returns:
        None.
    """
    from datetime import datetime, timezone

    # `initialize_database` is called on deliberately half-migrated
    # databases too: `tests/test_master_data.py` replays only the
    # migrations below 21 to reproduce an old customer number, and
    # restoring an old backup does the same thing for real. Neither has
    # this table yet, and neither is an error.
    has_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'message_template'"
    ).fetchone()
    if not has_table:
        return
    if connection.execute("SELECT 1 FROM message_template LIMIT 1").fetchone():
        return
    # Read positionally, not by column name: `initialize_database` is called
    # with whatever connection the caller has, and only some of them set
    # `row_factory = sqlite3.Row`. `_seed_default_settings` above never
    # noticed because it only tests the row for truth.
    settings = connection.execute(
        "SELECT invoice_email_subject, invoice_email_body, "
        "dunning1_email_subject, dunning1_email_body, "
        "dunning2_email_subject, dunning2_email_body "
        "FROM leg_settings WHERE id = 1"
    ).fetchone()
    if settings is None:
        return
    texts = dict(
        zip(
            (
                "invoice_email_subject",
                "invoice_email_body",
                "dunning1_email_subject",
                "dunning1_email_body",
                "dunning2_email_subject",
                "dunning2_email_body",
            ),
            tuple(settings),
        )
    )

    now = datetime.now(timezone.utc).isoformat()
    for name, occasion, subject_column, body_column, order in _CARRIED_OVER_TEMPLATES:
        connection.execute(
            """
            INSERT INTO message_template
                (name, occasion, step, trigger_kind, deadline_days,
                 subject, body, sort_order, created_at)
            VALUES (?, ?, '', '', NULL, ?, ?, ?, ?)
            """,
            (
                name,
                occasion,
                texts[subject_column] or "",
                texts[body_column] or "",
                order,
                now,
            ),
        )
    connection.commit()


def _split_sender_house_number(connection: sqlite3.Connection) -> None:
    """Move a trailing house number out of the LEG's street field.

    Until migration 54 the sender address had street and number in one box
    labelled "Strasse", which cost the administrator the number: the address
    check compares that box against street names, so "Im Feld 3" matched
    nothing, it offered "Im Feld", and accepting the suggestion wrote that
    over the whole value.

    Not done in the migration because "the last word, if it starts with a
    digit" needs a `reverse()` SQLite does not have -- and this is one row.

    Runs only while the new field is empty, so it cannot undo a correction.
    It converges by itself: once the number sits in its own field the street
    no longer ends in a digit, so a second pass finds nothing. A street
    deliberately typed as "Hauptstrasse 7" with the number field left empty
    is split too, which is the helpful reading of that state.

    Args:
        connection: Open SQLite connection, already migrated.

    Returns:
        None.
    """
    has_column = any(
        row[1] == "address_house_number" for row in connection.execute("PRAGMA table_info(leg_settings)")
    )
    if not has_column:
        # A deliberately half-migrated database, as when an old backup is
        # replayed -- see `_seed_message_templates` on the same point.
        return

    row = connection.execute(
        "SELECT address_street, address_house_number FROM leg_settings WHERE id = 1"
    ).fetchone()
    if row is None:
        return
    street, house_number = (row[0] or "").strip(), (row[1] or "").strip()
    if house_number or not street:
        return

    parts = street.rsplit(maxsplit=1)
    if len(parts) != 2 or not parts[1][:1].isdigit():
        return
    connection.execute(
        "UPDATE leg_settings SET address_street = ?, address_house_number = ? WHERE id = 1",
        (parts[0], parts[1]),
    )
    connection.commit()
