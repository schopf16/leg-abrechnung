"""Placeholder for the reports / plausibility checks page."""

from nicegui import ui

from app.gui.navigation import page_frame


@ui.page("/auswertungen")
def auswertungen_page() -> None:
    """Render the reports page (implemented in a later step).

    Returns:
        None.
    """
    with page_frame("/auswertungen", "Auswertungen"):
        ui.label("Wird in Kürze verfügbar.")
