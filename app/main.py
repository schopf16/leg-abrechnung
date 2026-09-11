"""Application entry point.

Initializes (and migrates) the local database, registers every GUI page and
starts NiceGUI as a self-contained native desktop window -- no separate
server process for the user to manage.
"""

import logging
import os
import platform

from nicegui import app, ui
from wsproto.utilities import LocalProtocolError

from app.db.connection import connection_scope
from app.db.schema import initialize_database
from app.gui.safe_notify import safe_notify
from app.logging_setup import configure_logging
from app.paths import ensure_directories
from app.version import APP_VERSION

configure_logging()

logger = logging.getLogger(__name__)


def _handle_ui_exception(exception: Exception) -> None:
    """Surface to Michael an error NiceGUI would otherwise log invisibly.

    NiceGUI already logs every exception raised inside an event handler (a
    button click, a form submit) via its own `nicegui` logger by default
    (`App._exception_handlers` starts as `[log.exception]`, see
    `nicegui.app.App.handle_exception`) -- that already reaches
    `logs/app.log` once `configure_logging()` has run, no extra logging
    needed here. What NiceGUI does *not* do by default is show the person
    anything: the click just silently appears to do nothing, which is the
    "es funktioniert einfach nicht" symptom this whole feature exists to
    fix. Registered via `app.on_exception` (NOT `ui.on_exception`, which
    requires an active per-client context and raises `RuntimeError:
    ui.page cannot be used in NiceGUI scripts...` when called this early,
    at startup, before any page has been rendered).

    Args:
        exception: The exception NiceGUI caught in an event handler.

    Returns:
        None.
    """
    safe_notify(
        f"Es ist ein Fehler aufgetreten ({type(exception).__name__}). "
        'Details siehe Log-Datei im Ordner „logs".',
        type="negative",
    )


def bootstrap() -> None:
    """Create required directories and bring the database schema up to date.

    Safe to call every time the application starts.

    Returns:
        None.
    """
    ensure_directories()
    with connection_scope() as connection:
        version = initialize_database(connection)
    logger.info("Database ready at schema version %s", version)


def main() -> None:
    """Start the NiceGUI native desktop window.

    Registers all page routes and blocks until the window is closed.

    Returns:
        None.
    """
    # A clear, greppable start marker -- `logs/app.log` is append-only
    # across restarts (never truncated, see app.logging_setup), so a quick
    # restart after an error does not erase the evidence. This banner is
    # what makes a restart visible at all when scanning the log: a START
    # line with no matching "ENDE (regulär beendet)" before the next START
    # means the previous run crashed rather than exited cleanly. Includes
    # enough environment detail (PID, Python/OS version) to be useful
    # handed to an AI for analysis without any further back-and-forth.
    logger.info("=" * 80)
    logger.info(
        "LEG-Abrechnung START -- Version %s, PID %s, Python %s, %s",
        APP_VERSION,
        os.getpid(),
        platform.python_version(),
        platform.platform(),
    )
    bootstrap()
    # Importing the pages package registers every @ui.page route with NiceGUI.
    from app.gui import pages  # noqa: F401

    # Registers the handler defined above so a UI-callback error also
    # shows something to Michael, not just NiceGUI's own default log entry
    # -- see the handler's docstring for why this must be `app.on_exception`
    # and not `ui.on_exception`.
    app.on_exception(_handle_ui_exception)

    # NOT logged as code after `ui.run()` below -- in native mode, closing
    # the window makes NiceGUI hard-exit the process via `os._exit()`
    # (see `nicegui.core.stop_and_exit`, whose own docstring says outright:
    # "Code after ui.run() ... still does not run"), so a plain trailing
    # log call would simply never fire and the ENDE half of the banner
    # would be permanently missing on every real close, not just crashes.
    # `app.on_shutdown` is explicitly run *before* that hard exit instead.
    def _log_clean_shutdown() -> None:
        logger.info("LEG-Abrechnung ENDE (regulär beendet).")
        logger.info("=" * 80)

    app.on_shutdown(_log_clean_shutdown)

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
        # `configure_logging()`'s root level is INFO, so a DEBUG call here
        # would be silently dropped, and a genuinely different
        # LocalProtocolError (same exception type, different root cause)
        # would leave no trace at all to diagnose from.
        logger.info("Ignored benign wsproto shutdown race.", exc_info=True)


if __name__ == "__main__":
    # Deliberately NOT `{"__main__", "__mp_main__"}` -- see run.py's own
    # guard for why including `__mp_main__` here would run `main()` a
    # second, redundant time in native mode's spawned window process.
    main()
