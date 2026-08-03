"""Application entry point.

Initializes (and migrates) the local database, registers every GUI page and
starts NiceGUI as a self-contained native desktop window -- no separate
server process for the user to manage.
"""

import logging

from nicegui import ui

from app.db.connection import connection_scope
from app.db.schema import initialize_database
from app.paths import ensure_directories

logging.basicConfig(level=logging.INFO)


def bootstrap() -> None:
    """Create required directories and bring the database schema up to date.

    Safe to call every time the application starts.

    Returns:
        None.
    """
    ensure_directories()
    with connection_scope() as connection:
        version = initialize_database(connection)
    logging.getLogger(__name__).info("Database ready at schema version %s", version)


def main() -> None:
    """Start the NiceGUI native desktop window.

    Registers all page routes and blocks until the window is closed.

    Returns:
        None.
    """
    bootstrap()
    # Importing the pages package registers every @ui.page route with NiceGUI.
    from app.gui import pages  # noqa: F401

    ui.run(
        title="LEG-Abrechnung",
        native=True,
        window_size=(1280, 860),
        reload=False,
        show=True,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
