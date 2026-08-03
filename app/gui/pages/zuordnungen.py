"""Placeholder for the meter-to-participant assignment history page."""

from nicegui import ui

from app.gui.navigation import page_frame


@ui.page("/zuordnungen")
def zuordnungen_page() -> None:
    """Render the assignments page (implemented in a later step).

    Returns:
        None.
    """
    with page_frame("/zuordnungen", "Zuordnungen"):
        ui.label("Wird in Kürze verfügbar.")
