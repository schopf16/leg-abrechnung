"""Placeholder for the EBIX/CSV import page."""

from nicegui import ui

from app.gui.navigation import page_frame


@ui.page("/import")
def import_page() -> None:
    """Render the import page (implemented in a later step).

    Returns:
        None.
    """
    with page_frame("/import", "Import"):
        ui.label("Wird in Kürze verfügbar.")
