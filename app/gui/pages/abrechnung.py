"""Placeholder for the billing run page."""

from nicegui import ui

from app.gui.navigation import page_frame


@ui.page("/abrechnung")
def abrechnung_page() -> None:
    """Render the billing page (implemented in a later step).

    Returns:
        None.
    """
    with page_frame("/abrechnung", "Abrechnung"):
        ui.label("Wird in Kürze verfügbar.")
