"""Shared onboarding-tracker edit dialog."""

from datetime import date, datetime
from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.form_dialog import form_guard
from app.gui.safe_notify import safe_notify
from app.models import leg as leg_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models.person import Person
from app.models.person_onboarding import STEPS, PersonOnboarding


def _parse_date(value: str) -> Optional[date]:
    """Parse a date string from a NiceGUI date input into a `date`."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def open_onboarding_form(
    onboarding: PersonOnboarding,
    person: Person,
    *,
    on_saved: Optional[Callable[[PersonOnboarding], None]] = None,
) -> None:
    """Open the edit dialog for one person's onboarding tracker."""
    with connection_scope() as connection:
        legs = leg_repo.list_all(connection)
    leg_options = {leg.id: leg.name for leg in legs}

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
        ui.label(f"Aufnahmeprozess: {person.display_name}").classes("text-lg font-bold")
        ui.label(
            "Datum je Schritt eintragen, sobald er erledigt ist. Kein "
            "Schritt ist Pflicht, die Reihenfolge wird nicht erzwungen."
        ).classes("text-caption text-grey-6")

        date_inputs: dict[str, ui.input] = {}
        for attr, label in STEPS:
            value = getattr(onboarding, attr)
            with ui.row().classes("w-full items-center gap-2"):
                date_inputs[attr] = (
                    ui.input(label, value=value.isoformat() if value else "")
                    .props("type=date")
                    .classes("flex-grow")
                )
                if attr == "leg_assigned_at":
                    leg_select = ui.select(
                        leg_options, label="LEG", value=onboarding.leg_id, with_input=True
                    ).classes("w-48")

        error_label = ui.label("").classes("text-negative")

        def save() -> None:
            """Validate the form and persist the onboarding tracker."""
            try:
                parsed = {attr: _parse_date(date_inputs[attr].value) for attr, _ in STEPS}
            except ValueError:
                error_label.text = "Ungültiges Datum."
                return

            onboarding.registered_at = parsed["registered_at"]
            onboarding.leg_assigned_at = parsed["leg_assigned_at"]
            onboarding.leg_id = leg_select.value
            onboarding.contract_signed_at = parsed["contract_signed_at"]
            onboarding.bkw_registered_at = parsed["bkw_registered_at"]
            onboarding.bkw_confirmed_at = parsed["bkw_confirmed_at"]

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
    form_guard(dialog, on_save=save)
    dialog.open()
