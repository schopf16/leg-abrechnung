"""Toast notification that tolerates a rare NiceGUI element-lifecycle race."""

import logging
from typing import Any

from nicegui import ui

logger = logging.getLogger(__name__)


def safe_notify(message: str, **kwargs: Any) -> None:
    """Show a toast notification, degrading to a log warning if the UI context it would attach to has..."""
    try:
        ui.notify(message, **kwargs)
    except RuntimeError:
        # ERROR, not WARNING, and with the traceback: this branch is the
        # one that hid a real bug for nine days (an upload handler raised,
        # the error notification could not be shown, and nothing reached
        # the screen). If it fires, that silence is itself the story.
        logger.error("Could not show notification (UI context already gone): %s", message, exc_info=True)
