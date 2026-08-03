"""Placeholder for the meters (Zähler) management page."""

from nicegui import ui

from app.gui.navigation import page_frame


@ui.page("/zaehler")
def zaehler_page() -> None:
    """Render the meters page (implemented in a later step).

    Returns:
        None.
    """
    with page_frame("/zaehler", "Zähler"):
        ui.label("Wird in Kürze verfügbar.")
