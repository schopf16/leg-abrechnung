"""Explicit, numbered database migrations.

Every schema change is expressed as a new entry in ``MIGRATIONS`` with the
next consecutive version number. Migrations are plain SQL scripts executed
in order; nothing is ever edited in place, so old backups can always be
brought up to the current schema by replaying the migrations they are
missing (see :mod:`app.db.schema`).

To add a schema change: append a new ``Migration`` with
``version = last_version + 1`` and a short ``description``. Never renumber
or remove existing entries.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    """A single, immutable schema migration step.

    Attributes:
        version: Target schema version this migration brings the database
            to. Must be exactly one higher than the previous migration.
        description: Short human-readable summary, shown in logs.
        sql: One or more SQL statements (semicolon separated) applied via
            ``executescript``.
    """

    version: int
    description: str
    sql: str


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        description="Initial schema: settings, participants, meters, "
        "assignments, readings, imports, billing runs.",
        sql="""
            CREATE TABLE leg_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL DEFAULT '',
                address_street TEXT NOT NULL DEFAULT '',
                address_zip TEXT NOT NULL DEFAULT '',
                address_city TEXT NOT NULL DEFAULT '',
                address_country TEXT NOT NULL DEFAULT 'CH',
                qr_iban TEXT NOT NULL DEFAULT '',
                price_rp_per_kwh REAL NOT NULL DEFAULT 12.0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address_street TEXT NOT NULL DEFAULT '',
                address_zip TEXT NOT NULL DEFAULT '',
                address_city TEXT NOT NULL DEFAULT '',
                address_country TEXT NOT NULL DEFAULT 'CH',
                iban TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE meters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metering_point_id TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL DEFAULT '',
                building_address TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL CHECK (
                    role IN ('bezug', 'produktion', 'bezug_fix', 'bezug_geschaltet')
                ),
                created_at TEXT NOT NULL
            );

            CREATE TABLE meter_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meter_id INTEGER NOT NULL REFERENCES meters(id) ON DELETE CASCADE,
                participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_assignments_meter ON meter_assignments(meter_id);
            CREATE INDEX idx_assignments_participant ON meter_assignments(participant_id);

            CREATE TABLE import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                format TEXT NOT NULL CHECK (format IN ('ebix', 'csv')),
                imported_at TEXT NOT NULL,
                period_from TEXT,
                period_to TEXT,
                row_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meter_id INTEGER NOT NULL REFERENCES meters(id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('bezug', 'produktion')),
                kwh REAL NOT NULL,
                source TEXT NOT NULL,
                import_batch_id INTEGER REFERENCES import_batches(id) ON DELETE SET NULL,
                UNIQUE (meter_id, timestamp, direction)
            );
            CREATE INDEX idx_readings_meter_ts ON readings(meter_id, timestamp);

            CREATE TABLE billing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_year INTEGER NOT NULL,
                period_quarter INTEGER NOT NULL CHECK (period_quarter BETWEEN 1 AND 4),
                created_at TEXT NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'erstellt',
                notes TEXT NOT NULL DEFAULT '',
                UNIQUE (period_year, period_quarter)
            );

            CREATE TABLE billing_run_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                billing_run_id INTEGER NOT NULL REFERENCES billing_runs(id) ON DELETE CASCADE,
                participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
                kind TEXT NOT NULL CHECK (kind IN ('rechnung', 'gutschrift')),
                kwh REAL NOT NULL,
                price_rp_per_kwh REAL NOT NULL,
                amount_rappen INTEGER NOT NULL,
                pdf_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_items_run ON billing_run_items(billing_run_id);
        """,
    ),
]
