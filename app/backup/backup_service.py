"""Manual, single-file database backups (project brief, section 8)."""

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.db.connection import connection_scope
from app.db.schema import CURRENT_SCHEMA_VERSION, get_schema_version, initialize_database
from app.paths import BACKUPS_DIR, DATABASE_PATH

#: Tables that must be present for a file to be accepted as a LEG database.
#: Validation runs BEFORE the restored file is migrated, so only tables
#: whose name has never changed across the migration history may be listed
#: here -- a backup taken at schema 38 still calls today's metering_point
#: table "messpunkt", and must remain restorable (that is the whole point
#: of replayable migrations).
_REQUIRED_TABLES = {"leg_settings", "person", "readings", "billing_runs"}

_BACKUP_FILENAME_PREFIX = "leg_abrechnung_"
_BACKUP_FILENAME_SUFFIX = ".sqlite3"


class BackupValidationError(Exception):
    """Raised when a file selected for restore is not a usable LEG backup."""


@dataclass(frozen=True)
class BackupContents:
    """How much master data a backup holds."""

    legs: int
    persons: int
    metering_points: int


@dataclass
class BackupFileInfo:
    """Metadata about one backup file for display in the UI."""

    path: Path
    created_at: datetime
    size_bytes: int
    contents: Optional[BackupContents] = None
    problem: Optional[str] = None

    @property
    def is_usable(self) -> bool:
        """Whether this file could be restored."""
        return self.problem is None


@dataclass
class RestoreResult:
    """Outcome of a successful restore operation."""

    safety_backup_path: Path
    restored_schema_version: int


def create_backup(db_path: Path = DATABASE_PATH, backups_dir: Path = BACKUPS_DIR) -> Path:
    """Write a consistent snapshot of the live database to `backups/`."""
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


def mirror_backup(backup_path: Path, extra_dir: Path) -> Optional[str]:
    """Copy an already-created backup file into a second, optional directory."""
    try:
        extra_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, extra_dir / backup_path.name)
    except OSError as exc:
        return (
            f"Zusätzlicher Backup-Pfad „{extra_dir}“ ist momentan nicht "
            f"erreichbar -- das Backup wurde trotzdem in backups/ "
            f"gespeichert. ({exc})"
        )
    return None


def list_backups(backups_dir: Path = BACKUPS_DIR) -> list[BackupFileInfo]:
    """List every file in the backups folder, most recent first."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    infos = []
    for path in backups_dir.iterdir():
        if not path.is_file():
            continue
        stat = path.stat()
        problem = check_backup_file(path)
        infos.append(
            BackupFileInfo(
                path=path,
                created_at=_created_at_of(path) or datetime.fromtimestamp(stat.st_mtime),
                size_bytes=stat.st_size,
                contents=read_backup_contents(path) if problem is None else None,
                problem=problem,
            )
        )
    # By when the snapshot was taken, not by filename: with names no
    # longer dictated, the name says nothing about the order.
    infos.sort(key=lambda info: info.created_at, reverse=True)
    return infos


def _read_only_uri(path: Path) -> str:
    """Build a read-only SQLite URI for a path, whatever it is called."""
    return f"{path.resolve().as_uri()}?mode=ro"


def _created_at_of(path: Path) -> Optional[datetime]:
    """Read the timestamp `create_backup` put into a backup's filename."""
    if not (path.name.startswith(_BACKUP_FILENAME_PREFIX) and path.suffix == _BACKUP_FILENAME_SUFFIX):
        return None
    stem = path.name[len(_BACKUP_FILENAME_PREFIX) : -len(_BACKUP_FILENAME_SUFFIX)]
    try:
        return datetime.strptime(stem, "%Y%m%d_%H%M%S_%f")
    except ValueError:
        return None


def read_backup_contents(path: Path) -> Optional[BackupContents]:
    """Count the master data inside a backup, without touching it."""
    try:
        connection = sqlite3.connect(_read_only_uri(path), uri=True)
    except sqlite3.Error:
        return None
    try:
        # Written out rather than looped over a list of table names: a
        # table name cannot be a bound parameter, so the loop meant
        # building SQL by string formatting, and no reader should have to
        # check whether those names are trustworthy.
        legs = connection.execute("SELECT COUNT(*) FROM leg").fetchone()[0]
        persons = connection.execute("SELECT COUNT(*) FROM person").fetchone()[0]
        metering_points = connection.execute("SELECT COUNT(*) FROM metering_point").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return BackupContents(legs=legs, persons=persons, metering_points=metering_points)


def _validate_backup_file(path: Path) -> None:
    """Check that a file is a structurally valid, non-corrupt LEG database."""
    if not path.exists():
        raise BackupValidationError(f"Datei nicht gefunden: {path}")

    try:
        connection = sqlite3.connect(_read_only_uri(path), uri=True)
    except sqlite3.Error as exc:
        raise BackupValidationError(f"Datei ist keine gültige SQLite-Datenbank: {exc}") from exc

    try:
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise BackupValidationError(f"Datei ist keine gültige SQLite-Datenbank: {exc}") from exc
        if integrity is None or integrity[0] != "ok":
            raise BackupValidationError(f"Backup-Datei ist beschädigt: {integrity}")

        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = _REQUIRED_TABLES - tables
        if missing:
            raise BackupValidationError(
                "Datei scheint keine LEG-Abrechnung-Datenbank zu sein "
                f"(fehlende Tabellen: {sorted(missing)})."
            )
    finally:
        connection.close()


def check_backup_file(path: Path) -> Optional[str]:
    """Ask whether a file could be restored, without raising."""
    try:
        _validate_backup_file(path)
    except BackupValidationError as exc:
        return str(exc)
    return None


def _read_schema_version(path: Path) -> int:
    """Read the schema version stored in a (already validated) database file."""
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
    """Replace the live database with the contents of a backup file."""
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

    return RestoreResult(safety_backup_path=safety_backup_path, restored_schema_version=restored_version)
