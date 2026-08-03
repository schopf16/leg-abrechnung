"""Placeholder for the backup / restore page."""

from nicegui import ui

from app.gui.navigation import page_frame


@ui.page("/backup")
def backup_page() -> None:
    """Render the backup page (implemented in a later step).

    Returns:
        None.
    """
    with page_frame("/backup", "Backup"):
        ui.label("Wird in Kürze verfügbar.")
