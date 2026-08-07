"""Manual, single-file database backups (project brief, section 8).

Deliberately simple: one backup = one timestamped `.sqlite3` file in
`backups/`. Restoring always takes a safety backup of the current database
first, then fully replaces it with the chosen backup's content and brings
it up to the current schema version, so old backups stay usable across
app upgrades.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.db.connection import connection_scope
from app.db.schema import CURRENT_SCHEMA_VERSION, get_schema_version, initialize_database
from app.paths import BACKUPS_DIR, DATABASE_PATH

#: Tables that must be present for a file to be accepted as a LEG database.
_REQUIRED_TABLES = {"leg_settings", "person", "messpunkt", "readings", "billing_runs"}

_BACKUP_FILENAME_PREFIX = "leg_abrechnung_"
_BACKUP_FILENAME_SUFFIX = ".sqlite3"


class BackupValidationError(Exception):
    """Raised when a file selected for restore is not a usable LEG backup."""


@dataclass
class BackupFileInfo:
    """Metadata about one backup file for display in the UI.

    Attributes:
        path: Filesystem path of the backup file.
        created_at: Timestamp the backup was written, from the filesystem.
        size_bytes: File size in bytes.
    """

    path: Path
    created_at: datetime
    size_bytes: int


@dataclass
class RestoreResult:
    """Outcome of a successful restore operation.

    Attributes:
        safety_backup_path: Path of the automatic safety backup taken of
            the database just before it was overwritten.
        restored_schema_version: Schema version the database is at after
            restoring and migrating.
    """

    safety_backup_path: Path
    restored_schema_version: int


def create_backup(
    db_path: Path = DATABASE_PATH, backups_dir: Path = BACKUPS_DIR
) -> Path:
    """Write a consistent snapshot of the live database to `backups/`.

    Uses SQLite's online backup API (rather than a plain file copy) so the
    snapshot is consistent even if a write happens to be in progress.

    Args:
        db_path: Path of the live database to snapshot.
        backups_dir: Directory to write the backup file into.

    Returns:
        Path of the newly created backup file.
    """
    backups_dir.mkdir(parents=True, exist_ok=True)
    # Microsecond precision avoids filename collisions when backups are
    # triggered in quick succession (e.g. the automatic safety backup
    # taken immediately before a restore).
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backups_dir / f"{_BACKUP_FILENAME_PREFIX}{timestamp}{_BACKUP_FILENAME_SUFFIX}"

    source = sqlite3.connect(str(db_path))
    try:
        destination = sqlite3.connect(str(backup_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    return backup_path


def list_backups(backups_dir: Path = BACKUPS_DIR) -> list[BackupFileInfo]:
    """List all backup files, most recent first.

    Args:
        backups_dir: Directory backups are stored in.

    Returns:
        Backup file metadata, sorted by filename (== chronologically,
        since filenames are timestamp-prefixed) descending.
    """
    backups_dir.mkdir(parents=True, exist_ok=True)
    infos = []
    for path in sorted(
        backups_dir.glob(f"{_BACKUP_FILENAME_PREFIX}*{_BACKUP_FILENAME_SUFFIX}"), reverse=True
    ):
        stat = path.stat()
        infos.append(
            BackupFileInfo(
                path=path,
                created_at=datetime.fromtimestamp(stat.st_mtime),
                size_bytes=stat.st_size,
            )
        )
    return infos


def _validate_backup_file(path: Path) -> None:
    """Check that a file is a structurally valid, non-corrupt LEG database.

    Args:
        path: Candidate backup file.

    Returns:
        None.

    Raises:
        BackupValidationError: If the file cannot be opened as SQLite, is
            reported corrupt by `PRAGMA integrity_check`, or is missing
            tables a LEG database must have.
    """
    if not path.exists():
        raise BackupValidationError(f"Datei nicht gefunden: {path}")

    try:
        connection = sqlite3.connect(str(path))
    except sqlite3.Error as exc:
        raise BackupValidationError(f"Datei ist keine gültige SQLite-Datenbank: {exc}") from exc

    try:
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise BackupValidationError(
                f"Datei ist keine gültige SQLite-Datenbank: {exc}"
            ) from exc
        if integrity is None or integrity[0] != "ok":
            raise BackupValidationError(f"Backup-Datei ist beschädigt: {integrity}")

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = _REQUIRED_TABLES - tables
        if missing:
            raise BackupValidationError(
                "Datei scheint keine LEG-Abrechnung-Datenbank zu sein "
                f"(fehlende Tabellen: {sorted(missing)})."
            )
    finally:
        connection.close()


def _read_schema_version(path: Path) -> int:
    """Read the schema version stored in a (already validated) database file.

    Args:
        path: Path of the database file.

    Returns:
        The stored schema version, or `0` if unset.
    """
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    try:
        return get_schema_version(connection)
    finally:
        connection.close()


def restore_backup(
    backup_path: Path,
    db_path: Path = DATABASE_PATH,
    backups_dir: Path = BACKUPS_DIR,
) -> RestoreResult:
    """Replace the live database with the contents of a backup file.

    Always takes an automatic safety backup of the current database first
    (so restoring is itself undoable), then fully replaces the live
    database and migrates it to the current schema version -- this is what
    lets an old backup, taken by an earlier version of the app, keep
    working after the app has been upgraded.

    Args:
        backup_path: Path of the backup file to restore.
        db_path: Path of the live database to overwrite.
        backups_dir: Directory to write the automatic safety backup into.

    Returns:
        A `RestoreResult` with the safety backup's path and the resulting
        schema version.

    Raises:
        BackupValidationError: If `backup_path` is not a valid, intact LEG
            database, or was created by a newer, incompatible app version.
    """
    _validate_backup_file(backup_path)

    backup_version = _read_schema_version(backup_path)
    if backup_version > CURRENT_SCHEMA_VERSION:
        raise BackupValidationError(
            f"Diese Backup-Datei hat Schema-Version {backup_version}, die "
            f"installierte App unterstützt nur bis Version {CURRENT_SCHEMA_VERSION}. "
            "Bitte zuerst die App aktualisieren."
        )

    safety_backup_path = create_backup(db_path, backups_dir)

    source = sqlite3.connect(str(backup_path))
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        destination = sqlite3.connect(str(db_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    with connection_scope(db_path) as connection:
        restored_version = initialize_database(connection)

    return RestoreResult(
        safety_backup_path=safety_backup_path, restored_schema_version=restored_version
    )
