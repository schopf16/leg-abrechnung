"""Placeholder for the participants (Teilnehmer) management page."""

from nicegui import ui

from app.gui.navigation import page_frame


@ui.page("/teilnehmer")
def teilnehmer_page() -> None:
    """Render the participants page (implemented in a later step).

    Returns:
        None.
    """
    with page_frame("/teilnehmer", "Teilnehmer"):
        ui.label("Wird in Kürze verfügbar.")
