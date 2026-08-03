"""Placeholder for the LEG settings page."""

from nicegui import ui

from app.gui.navigation import page_frame


@ui.page("/einstellungen")
def einstellungen_page() -> None:
    """Render the settings page (implemented in a later step).

    Returns:
        None.
    """
    with page_frame("/einstellungen", "Einstellungen"):
        ui.label("Wird in Kürze verfügbar.")
