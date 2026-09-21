"""Manual, single-file database backups (project brief, section 8).

Deliberately simple: one backup = one timestamped `.sqlite3` file in
`backups/`. Restoring always takes a safety backup of the current database
first, then fully replaces it with the chosen backup's content and brings
it up to the current schema version, so old backups stay usable across
app upgrades.
"""

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
    """How much master data a backup holds.

    A filename and a size say nothing about which backup is which. These
    three numbers do: they are what changes as the community grows, so
    they are what tells two snapshots apart at a glance.

    Attributes:
        legs: Number of LEGs.
        persons: Number of persons.
        metering_points: Number of metering points.
    """

    legs: int
    persons: int
    metering_points: int


@dataclass
class BackupFileInfo:
    """Metadata about one backup file for display in the UI.

    Attributes:
        path: Filesystem path of the backup file.
        created_at: When the backup was taken, read from its own filename
            (see `create_backup`), falling back to the file's
            modification time for anything not named that way. The
            filename is preferred because copying a backup around
            rewrites the modification time while the name keeps saying
            when the snapshot was actually made.
        size_bytes: File size in bytes.
        contents: What is inside, or `None` if the counts could not be
            read -- a backup from an older schema names its tables
            differently. `None` is shown as such rather than as zeroes,
            which would read like an empty database. A file can be
            perfectly restorable and still have no counts.
        problem: Why this file cannot be restored, or `None` if it can.
    """

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
    """Outcome of a successful restore operation.

    Attributes:
        safety_backup_path: Path of the automatic safety backup taken of
            the database just before it was overwritten.
        restored_schema_version: Schema version the database is at after
            restoring and migrating.
    """

    safety_backup_path: Path
    restored_schema_version: int


def create_backup(db_path: Path = DATABASE_PATH, backups_dir: Path = BACKUPS_DIR) -> Path:
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


def mirror_backup(backup_path: Path, extra_dir: Path) -> Optional[str]:
    """Copy an already-created backup file into a second, optional directory.

    Lets the administrator keep a copy on an external location (e.g. a
    mapped network drive) in addition to the primary copy in `backups/`.
    Deliberately never raises: if the extra location is temporarily
    unreachable (e.g. while travelling), the primary backup already
    written to `backups/` is unaffected -- this only reports the problem
    back to the caller instead of crashing.

    Args:
        backup_path: Backup file already written to `backups/`.
        extra_dir: Directory to also copy it into.

    Returns:
        `None` if the copy succeeded, or a short German warning message
        describing why it did not.
    """
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
    """List every file in the backups folder, most recent first.

    Deliberately not filtered by filename. Whether a file can be restored
    is a question about its contents, and asking the filename instead hid
    real backups: a copy saved under a describing name -- exactly what one
    does before something risky -- was simply absent from the list, with
    nothing saying why. Every file is opened and asked directly; the ones
    that turn out not to be LEG databases stay in the list carrying the
    reason, so "why is my file not here" cannot arise either.

    Args:
        backups_dir: Directory backups are stored in.

    Returns:
        Backup file metadata, newest first.
    """
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


def _created_at_of(path: Path) -> Optional[datetime]:
    """Read the timestamp `create_backup` put into a backup's filename.

    Args:
        path: The backup file.

    Returns:
        The moment the snapshot was taken, or `None` if the name does not
        carry one (a file put here by hand, or renamed).
    """
    if not (path.name.startswith(_BACKUP_FILENAME_PREFIX) and path.suffix == _BACKUP_FILENAME_SUFFIX):
        return None
    stem = path.name[len(_BACKUP_FILENAME_PREFIX) : -len(_BACKUP_FILENAME_SUFFIX)]
    try:
        return datetime.strptime(stem, "%Y%m%d_%H%M%S_%f")
    except ValueError:
        return None


def read_backup_contents(path: Path) -> Optional[BackupContents]:
    """Count the master data inside a backup, without touching it.

    Opened strictly read-only and never migrated: a backup is evidence of
    a past state, and reading it must not change what it says. A file
    from an older schema simply has no answer here -- reporting zeroes
    would be worse than reporting nothing, since an empty community and
    an unreadable file are very different things.

    Args:
        path: The backup file.

    Returns:
        Its `BackupContents`, or `None` if it cannot be read.
    """
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
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
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
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
    """Ask whether a file could be restored, without raising.

    The same question `restore_backup` asks, phrased for a list rather
    than for a failure: a file either is a LEG database this app can read
    back, or there is a reason it is not, and that reason is worth
    showing beside it.

    Args:
        path: Candidate backup file.

    Returns:
        `None` if the file is usable, else a short German explanation.
    """
    try:
        _validate_backup_file(path)
    except BackupValidationError as exc:
        return str(exc)
    return None


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

    return RestoreResult(safety_backup_path=safety_backup_path, restored_schema_version=restored_version)
