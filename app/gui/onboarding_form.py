"""Shared onboarding-tracker edit dialog.

Used both by the Aufnahmen page and the Person detail page, so a tracker
can be edited directly from wherever the administrator happens to be
looking at it, without a page change -- mirrors how `app.gui.person_form`
is shared between the Personen and Web-Registrierungen pages.
"""

from datetime import date, datetime
from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.safe_notify import safe_notify
from app.models import leg as leg_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models.person import Person
from app.models.person_onboarding import STEPS, PersonOnboarding


def _parse_date(value: str) -> Optional[date]:
    """Parse a date string from a NiceGUI date input into a `date`.

    Args:
        value: Date string in ISO format ("YYYY-MM-DD"), or empty/`None`.

    Returns:
        The parsed `date`, or `None` if `value` is empty.
    """
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def open_onboarding_form(
    onboarding: PersonOnboarding,
    person: Person,
    *,
    on_saved: Optional[Callable[[PersonOnboarding], None]] = None,
) -> None:
    """Open the edit dialog for one person's onboarding tracker.

    Args:
        onboarding: Tracker to edit (must already exist -- this dialog
            never creates one, see `app.models.person_onboarding.
            start_for_person` for that).
        person: The tracked person, for display and the dialog title.
        on_saved: Called with the updated `PersonOnboarding` after a
            successful save (dialog already closed).

    Returns:
        None.
    """
    with connection_scope() as connection:
        legs = leg_repo.list_all(connection)
    leg_options = {leg.id: leg.name for leg in legs}

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
        ui.label(f"Aufnahmeprozess: {person.anzeige_name}").classes("text-lg font-bold")
        ui.label(
            "Datum je Schritt eintragen, sobald er erledigt ist. Kein "
            "Schritt ist Pflicht, die Reihenfolge wird nicht erzwungen."
        ).classes("text-caption text-grey-6")

        date_inputs: dict[str, ui.input] = {}
        for attr, label in STEPS:
            value = getattr(onboarding, attr)
            with ui.row().classes("w-full items-center gap-2"):
                date_inputs[attr] = ui.input(
                    label, value=value.isoformat() if value else ""
                ).props("type=date").classes("flex-grow")
                if attr == "leg_zugewiesen_am":
                    leg_select = ui.select(
                        leg_options, label="LEG", value=onboarding.leg_id, with_input=True
                    ).classes("w-48")

        error_label = ui.label("").classes("text-negative")

        def save() -> None:
            """Validate the form and persist the onboarding tracker.

            Returns:
                None.
            """
            try:
                parsed = {attr: _parse_date(date_inputs[attr].value) for attr, _ in STEPS}
            except ValueError:
                error_label.text = "Ungültiges Datum."
                return

            onboarding.angemeldet_am = parsed["angemeldet_am"]
            onboarding.leg_zugewiesen_am = parsed["leg_zugewiesen_am"]
            onboarding.leg_id = leg_select.value
            onboarding.vertrag_unterzeichnet_am = parsed["vertrag_unterzeichnet_am"]
            onboarding.bkw_angemeldet_am = parsed["bkw_angemeldet_am"]
            onboarding.bkw_bestaetigt_am = parsed["bkw_bestaetigt_am"]

            with connection_scope() as connection:
                person_onboarding_repo.update(connection, onboarding)
            dialog.close()
            # notify before on_saved(): see app.gui.safe_notify -- a caller's
            # on_saved may refresh a card-based list, which can tear down
            # this dialog's own UI context first.
            safe_notify("Gespeichert.", type="positive")
            if on_saved:
                on_saved(onboarding)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            ui.button("Speichern", on_click=save)
    dialog.open()
