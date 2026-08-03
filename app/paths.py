"""Central definitions of on-disk locations used by the application.

All paths that may contain personal or otherwise sensitive data live outside
the source tree (in ``data/``, ``output/`` and ``backups/``) so that the
project's ``.gitignore`` can exclude them reliably.
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

#: Path of the live SQLite database file.
DATABASE_PATH = DATA_DIR / "leg_abrechnung.sqlite3"


def ensure_directories() -> None:
    """Create the data, output and backup directories if they do not exist yet.

    Returns:
        None.
    """
    for directory in (DATA_DIR, OUTPUT_DIR, BACKUPS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
