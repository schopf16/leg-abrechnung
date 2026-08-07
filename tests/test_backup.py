"""Tests for manual database backup and restore."""

import sqlite3

import pytest

from app.backup.backup_service import (
    BackupValidationError,
    create_backup,
    list_backups,
    restore_backup,
)
from app.db.connection import create_connection
from app.db.schema import initialize_database
from app.models import person as person_repo
from app.models.person import Person


def _make_live_db(path) -> None:
    """Initialize a schema-migrated database at `path` with one person.

    Args:
        path: Filesystem path to create the database at.

    Returns:
        None.
    """
    connection = create_connection(path)
    initialize_database(connection)
    person_repo.create(
        connection,
        Person(
            id=None, anrede="", name="Original", kontakt_email="", kontakt_telefon="",
            rechnungsadresse_strasse="", rechnungsadresse_plz="",
            rechnungsadresse_ort="", rechnungsadresse_land="CH",
            iban="", kundennummer=None, papierrechnung=False, created_at="",
        ),
    )
    connection.close()


def test_create_backup_produces_restorable_snapshot(tmp_path):
    """A created backup file contains the same data as the live database."""
    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)

    backup_path = create_backup(db_path, backups_dir)

    assert backup_path.exists()
    connection = sqlite3.connect(str(backup_path))
    names = [row[0] for row in connection.execute("SELECT name FROM person")]
    connection.close()
    assert names == ["Original"]


def test_list_backups_returns_newest_first(tmp_path):
    """Backups are listed most recent first."""
    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)

    first = create_backup(db_path, backups_dir)
    second = create_backup(db_path, backups_dir)

    backups = list_backups(backups_dir)
    paths = [b.path for b in backups]
    assert paths == [second, first]


def test_restore_backup_replaces_live_database_and_creates_safety_backup(tmp_path):
    """Restoring overwrites the live DB and keeps a safety backup of the old state."""
    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)
    old_backup_path = create_backup(db_path, backups_dir)

    # Change the live database after the backup was taken.
    connection = create_connection(db_path)
    person_repo.create(
        connection,
        Person(
            id=None, anrede="", name="Added later", kontakt_email="", kontakt_telefon="",
            rechnungsadresse_strasse="", rechnungsadresse_plz="",
            rechnungsadresse_ort="", rechnungsadresse_land="CH",
            iban="", kundennummer=None, papierrechnung=False, created_at="",
        ),
    )
    connection.close()

    result = restore_backup(old_backup_path, db_path, backups_dir)

    # The live DB is back to the pre-change (backed up) state.
    connection = create_connection(db_path)
    names = {row["name"] for row in connection.execute("SELECT name FROM person")}
    connection.close()
    assert names == {"Original"}

    # A safety backup of the state just before restoring was taken.
    assert result.safety_backup_path.exists()
    safety_connection = sqlite3.connect(str(result.safety_backup_path))
    safety_names = {row[0] for row in safety_connection.execute("SELECT name FROM person")}
    safety_connection.close()
    assert safety_names == {"Original", "Added later"}


def test_restore_backup_rejects_non_database_file(tmp_path):
    """Restoring from a file that isn't a SQLite database is rejected with a clear error."""
    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)

    bogus_file = tmp_path / "not_a_database.sqlite3"
    bogus_file.write_text("this is not a database")

    with pytest.raises(BackupValidationError):
        restore_backup(bogus_file, db_path, backups_dir)


def test_restore_backup_rejects_database_missing_expected_tables(tmp_path):
    """Restoring from a SQLite file that isn't a LEG database is rejected."""
    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)

    unrelated_db_path = tmp_path / "unrelated.sqlite3"
    connection = sqlite3.connect(str(unrelated_db_path))
    connection.execute("CREATE TABLE something_else (id INTEGER)")
    connection.commit()
    connection.close()

    with pytest.raises(BackupValidationError):
        restore_backup(unrelated_db_path, db_path, backups_dir)


def test_restore_backup_migrates_database_to_current_schema(tmp_path):
    """Restoring a backup runs schema migration/seeding, leaving a usable database."""
    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)
    backup_path = create_backup(db_path, backups_dir)

    result = restore_backup(backup_path, db_path, backups_dir)

    from app.db.schema import CURRENT_SCHEMA_VERSION

    assert result.restored_schema_version == CURRENT_SCHEMA_VERSION
    connection = create_connection(db_path)
    # leg_settings row must have been (re-)seeded by initialize_database.
    settings_row = connection.execute("SELECT * FROM leg_settings WHERE id = 1").fetchone()
    connection.close()
    assert settings_row is not None
