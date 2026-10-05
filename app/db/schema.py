"""Schema versioning and migration runner."""

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
    """Create the ``schema_meta`` bookkeeping table if it is missing."""
    connection.execute(_META_TABLE_SQL)
    connection.commit()


def get_schema_version(connection: sqlite3.Connection) -> int:
    """Read the schema version currently stored in the database."""
    _ensure_meta_table(connection)
    row = connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 0


def _set_schema_version(connection: sqlite3.Connection, version: int) -> None:
    """Persist the schema version after a successful migration."""
    connection.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


def migrate_to_latest(connection: sqlite3.Connection) -> int:
    """Apply all pending migrations to bring the database up to date."""
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
    """Ensure a database connection is ready for use by the application."""
    version = migrate_to_latest(connection)
    _seed_default_settings(connection)
    _seed_message_templates(connection)
    _split_sender_house_number(connection)
    return version


def _seed_default_settings(connection: sqlite3.Connection) -> None:
    """Insert the single default LEG settings row if it does not exist."""
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
    """Carry the invoice and dunning texts into `message_template`."""
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
    """Move a trailing house number out of the LEG's street field."""
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
