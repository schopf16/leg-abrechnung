"""Central definitions of on-disk locations used by the application.

All paths that may contain personal or otherwise sensitive data live outside
the source tree (in ``data/``, ``output/``, ``backups/`` and ``logs/``) so
that the project's ``.gitignore`` can exclude them reliably.
"""

from pathlib import Path

#: Root directory of the whole project (parent of the ``app`` package).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Directory holding the SQLite database file. Gitignored.
DATA_DIR = PROJECT_ROOT / "data"

#: Directory holding generated PDFs and payment lists. Gitignored.
OUTPUT_DIR = PROJECT_ROOT / "output"

#: Directory holding manual database backups. Gitignored.
BACKUPS_DIR = PROJECT_ROOT / "backups"

#: Directory holding the rotating application log file. Gitignored (log
#: lines can contain real names/amounts from error messages).
LOGS_DIR = PROJECT_ROOT / "logs"

#: Path of the live SQLite database file.
DATABASE_PATH = DATA_DIR / "leg_abrechnung.sqlite3"


def ensure_directories() -> None:
    """Create the data, output, backup and log directories if missing.

    Returns:
        None.
    """
    for directory in (DATA_DIR, OUTPUT_DIR, BACKUPS_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
