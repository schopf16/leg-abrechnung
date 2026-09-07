"""Application entry point.

Initializes (and migrates) the local database, registers every GUI page and
starts NiceGUI as a self-contained native desktop window -- no separate
server process for the user to manage.
"""

import logging

from nicegui import app, ui
from wsproto.utilities import LocalProtocolError

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

    # A normal window (title bar, minimize/maximize/close buttons),
    # just opened already maximized -- NOT `fullscreen=True`, which opens
    # a borderless window with no way to minimize it. `maximized` is a
    # pywebview-level `create_window()` argument with no direct `ui.run()`
    # parameter of its own, so it goes through `app.native.window_args`
    # (merged into the pywebview call, see nicegui's native_mode.py).
    app.native.window_args["maximized"] = True

    try:
        ui.run(
            title="LEG-Abrechnung",
            native=True,
            window_size=(1280, 860),
            reload=False,
            show=True,
        )
    except LocalProtocolError:
        # A known, purely cosmetic uvicorn/wsproto race when the native
        # window's websocket connection is already gone by the time
        # uvicorn's shutdown sequence tries to close it (happens after
        # quitting via the "Beenden" button or the window's own close
        # button -- see e.g. zauberzeug/nicegui#5845). By this point the
        # app has already run and shut down; letting this escape would
        # make start.bat report a nonzero exit code as a false "Fehler
        # beendet", even though nothing actually went wrong.
        # Logged at INFO (not DEBUG) despite being expected/benign --
        # `logging.basicConfig(level=logging.INFO)` above means a DEBUG
        # call here would be silently dropped, so a genuinely different
        # LocalProtocolError (same exception type, different root cause)
        # would leave no trace at all to diagnose from.
        logging.getLogger(__name__).info(
            "Ignored benign wsproto shutdown race.", exc_info=True
        )


if __name__ in {"__main__", "__mp_main__"}:
    main()
