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
            id=None,
            salutation="",
            company="",
            first_name="Original",
            last_name="",
            contact_email="",
            contact_phone="",
            billing_street="",
            billing_house_number="",
            billing_postal_code="",
            billing_city="",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
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
    names = [row[0] for row in connection.execute("SELECT first_name FROM person")]
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
            id=None,
            salutation="",
            company="",
            first_name="Added later",
            last_name="",
            contact_email="",
            contact_phone="",
            billing_street="",
            billing_house_number="",
            billing_postal_code="",
            billing_city="",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
    )
    connection.close()

    result = restore_backup(old_backup_path, db_path, backups_dir)

    # The live DB is back to the pre-change (backed up) state.
    connection = create_connection(db_path)
    names = {row["first_name"] for row in connection.execute("SELECT first_name FROM person")}
    connection.close()
    assert names == {"Original"}

    # A safety backup of the state just before restoring was taken.
    assert result.safety_backup_path.exists()
    safety_connection = sqlite3.connect(str(result.safety_backup_path))
    safety_names = {row[0] for row in safety_connection.execute("SELECT first_name FROM person")}
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


def test_restore_backup_accepts_backup_from_before_table_renames(tmp_path, monkeypatch):
    """A backup taken before migration 39 still calls the metering-point
    table "messpunkt". Validation runs before the restored file is migrated,
    so it must only require tables whose name never changed -- otherwise
    every pre-rename backup becomes un-restorable, which is exactly what
    replayable migrations exist to prevent."""
    import app.db.schema as schema_module
    from app.db.migrations import MIGRATIONS
    from app.db.schema import CURRENT_SCHEMA_VERSION

    old_backup_path = tmp_path / "leg_abrechnung_20260901_000000_000000.sqlite3"
    connection = sqlite3.connect(old_backup_path)
    connection.row_factory = sqlite3.Row
    monkeypatch.setattr(schema_module, "MIGRATIONS", [m for m in MIGRATIONS if m.version <= 38])
    schema_module.initialize_database(connection)
    assert "messpunkt" in {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    connection.close()
    monkeypatch.setattr(schema_module, "MIGRATIONS", MIGRATIONS)

    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)

    result = restore_backup(old_backup_path, db_path, backups_dir)

    assert result.restored_schema_version == CURRENT_SCHEMA_VERSION


# --- Telling one backup from another ------------------------------------
#
# A filename and a size say nothing about which state a snapshot holds,
# which is the whole reason the list carries the counts.


def test_a_backup_reports_what_is_inside_it(tmp_path):
    """The counts are what makes two snapshots distinguishable."""
    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)

    create_backup(db_path, backups_dir)
    (info,) = list_backups(backups_dir)

    assert info.contents is not None
    assert info.contents.persons == 1
    assert info.contents.legs == 0
    assert info.contents.metering_points == 0


def test_the_creation_time_comes_from_the_filename_not_the_file(tmp_path):
    """Copying a backup rewrites its modification time; the name still holds.

    The name is what `create_backup` stamped at the moment the snapshot
    was taken, so it is the honest answer to "when is this from".
    """
    import os
    from datetime import datetime

    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)
    backup_path = create_backup(db_path, backups_dir)

    # Pretend the file was touched much later, as a copy would be.
    later = datetime(2030, 1, 1, 12, 0).timestamp()
    os.utime(backup_path, (later, later))

    (info,) = list_backups(backups_dir)

    assert info.created_at.year != 2030, "die Dateizeit darf das Datum nicht verfälschen"
    assert info.created_at.strftime("%Y%m%d_%H%M%S") in backup_path.name


def test_a_backup_from_an_older_schema_reports_nothing_rather_than_zero(tmp_path):
    """ "Unreadable" and "empty" must not look the same.

    Real backups predate the English table names; showing them as
    0 LEG / 0 Personen would invite restoring one in the belief it holds
    nothing worth keeping.
    """
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    old = backups_dir / "leg_abrechnung_20250101_120000_000000.sqlite3"
    connection = sqlite3.connect(str(old))
    connection.execute("CREATE TABLE personen (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    (info,) = list_backups(backups_dir)

    assert info.contents is None


def test_the_counts_never_modify_the_backup(tmp_path):
    """Reading a backup must not change what it says -- it is evidence."""
    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)
    backup_path = create_backup(db_path, backups_dir)
    before = backup_path.read_bytes()

    list_backups(backups_dir)

    assert backup_path.read_bytes() == before


def test_a_backup_saved_under_a_describing_name_is_listed(tmp_path):
    """Whether a file is a backup is a question about its contents.

    Filtering on the filename hid real backups: a copy saved under a
    describing name -- exactly what one does before something risky --
    was simply absent, with nothing anywhere saying why.
    """
    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)
    created = create_backup(db_path, backups_dir)
    renamed = backups_dir / "echtdaten_vor_dem_umbau.sqlite3"
    created.rename(renamed)

    (info,) = list_backups(backups_dir)

    assert info.path == renamed
    assert info.is_usable
    assert info.contents.persons == 1


def test_a_file_that_is_not_a_leg_database_is_listed_with_the_reason(tmp_path):
    """ "Why is my file not in the list" must have an answer on the page."""
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    (backups_dir / "notizen.txt").write_text("kein Backup", encoding="utf-8")

    (info,) = list_backups(backups_dir)

    assert not info.is_usable
    assert info.problem
    assert info.contents is None


def test_a_sqlite_file_from_another_program_is_refused(tmp_path):
    """Being valid SQLite is not enough -- it has to be one of ours."""
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    foreign = backups_dir / "adressen.sqlite3"
    connection = sqlite3.connect(str(foreign))
    connection.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    (info,) = list_backups(backups_dir)

    assert not info.is_usable
    assert "LEG-Abrechnung" in info.problem


def test_directories_in_the_backups_folder_are_ignored(tmp_path):
    """A folder is not a file and must not appear as a broken backup."""
    backups_dir = tmp_path / "backups"
    (backups_dir / "archiv").mkdir(parents=True)

    assert list_backups(backups_dir) == []


def test_backups_are_listed_newest_first_whatever_they_are_called(tmp_path):
    """With names no longer dictated, the order cannot come from them."""
    import os
    from datetime import datetime

    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)

    older = create_backup(db_path, backups_dir).rename(backups_dir / "zzz_alt.sqlite3")
    newer = create_backup(db_path, backups_dir).rename(backups_dir / "aaa_neu.sqlite3")
    os.utime(older, (datetime(2020, 1, 1).timestamp(),) * 2)
    os.utime(newer, (datetime(2030, 1, 1).timestamp(),) * 2)

    assert [b.path for b in list_backups(backups_dir)] == [newer, older]


def test_listing_never_writes_beside_the_files_it_inspects(tmp_path):
    """Opening a backup to check it must not leave journal files behind."""
    db_path = tmp_path / "live.sqlite3"
    backups_dir = tmp_path / "backups"
    _make_live_db(db_path)
    create_backup(db_path, backups_dir)
    before = sorted(p.name for p in backups_dir.iterdir())

    list_backups(backups_dir)

    assert sorted(p.name for p in backups_dir.iterdir()) == before
