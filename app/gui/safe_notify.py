"""Toast notification that tolerates a rare NiceGUI element-lifecycle race.

`ui.notify()` resolves its target client via the currently active UI
"slot" context. On a card-based list (Personen, Zuordnungen, Messpunkte),
a delete/save action's confirm dialog or edit dialog is opened from a
button that lives inside a card the subsequent `refresh()` call clears and
rebuilds. NiceGUI auto-deletes a dialog once the context it was created in
is torn down (its internal "canary" element, used to detect exactly that),
which can -- depending on what else in the object graph is still keeping
things alive at that moment -- leave the notify call's slot context
referencing an already-deleted element, raising
``RuntimeError: The parent element this slot belongs to has been deleted.``
(reported in production, see git history around this module's introduction).

By the time any of these notifications fire, the underlying database
mutation has already been committed, so failing to show the toast loses
nothing but the confirmation message -- silently logging a warning instead
of propagating an unhandled exception is the right tradeoff.
"""

import logging
from typing import Any

from nicegui import ui

logger = logging.getLogger(__name__)


def safe_notify(message: str, **kwargs: Any) -> None:
    """Show a toast notification, degrading to a log warning if the UI
    context it would attach to has already been torn down.

    Args:
        message: Notification text.
        **kwargs: Forwarded to `ui.notify` (e.g. `type`, `timeout`).

    Returns:
        None.
    """
    try:
        ui.notify(message, **kwargs)
    except RuntimeError:
        logger.warning("Could not show notification (UI context already gone): %s", message)
