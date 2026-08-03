"""Tests for the generic, numbered migration runner (independent of however
many real migrations `app.db.migrations.MIGRATIONS` currently has)."""

import sqlite3

import app.db.schema as schema_module
from app.db.migrations import Migration


def _fresh_connection() -> sqlite3.Connection:
    """Open a fresh in-memory SQLite connection with row access by name.

    Returns:
        A new `sqlite3.Connection`.
    """
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    return connection


def test_migrate_to_latest_applies_all_pending_migrations_in_order(monkeypatch):
    """A brand-new database is brought from version 0 to the newest version."""
    fake_migrations = [
        Migration(1, "create table", "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT);"),
        Migration(2, "add column", "ALTER TABLE t ADD COLUMN w TEXT;"),
    ]
    monkeypatch.setattr(schema_module, "MIGRATIONS", fake_migrations)

    connection = _fresh_connection()
    version = schema_module.migrate_to_latest(connection)

    assert version == 2
    connection.execute("INSERT INTO t (v, w) VALUES ('a', 'b')")  # column w must exist
    assert schema_module.get_schema_version(connection) == 2


def test_migrate_to_latest_only_applies_missing_migrations(monkeypatch):
    """Re-running migrate_to_latest on an already-migrated database is a no-op."""
    fake_migrations = [
        Migration(1, "create table", "CREATE TABLE t (id INTEGER PRIMARY KEY);"),
    ]
    monkeypatch.setattr(schema_module, "MIGRATIONS", fake_migrations)
    connection = _fresh_connection()
    schema_module.migrate_to_latest(connection)

    # Running again must not try to re-execute "CREATE TABLE t", which
    # would fail since the table already exists.
    version = schema_module.migrate_to_latest(connection)
    assert version == 1


def test_migrate_to_latest_upgrades_an_old_partial_database(monkeypatch):
    """A database stopped at an earlier version only gets the newer migrations applied.

    Simulates opening an old backup (created back when only migration 1
    existed) with a newer app build that also knows migration 2.
    """
    migration_1 = Migration(1, "create table", "CREATE TABLE t (id INTEGER PRIMARY KEY);")
    migration_2 = Migration(2, "create second table", "CREATE TABLE t2 (id INTEGER PRIMARY KEY);")

    # Step 1: an "old" database only knows about migration 1.
    monkeypatch.setattr(schema_module, "MIGRATIONS", [migration_1])
    connection = _fresh_connection()
    schema_module.migrate_to_latest(connection)
    assert schema_module.get_schema_version(connection) == 1

    # Step 2: the "new" app build knows about migration 2 as well; opening
    # the same (already migration-1) database must only apply migration 2,
    # not re-run migration 1.
    monkeypatch.setattr(schema_module, "MIGRATIONS", [migration_1, migration_2])
    version = schema_module.migrate_to_latest(connection)

    assert version == 2
    # Both the old and the new table must exist.
    connection.execute("SELECT * FROM t")
    connection.execute("SELECT * FROM t2")


def test_get_schema_version_is_zero_for_brand_new_database():
    """A never-migrated database reports schema version 0."""
    connection = _fresh_connection()
    assert schema_module.get_schema_version(connection) == 0
