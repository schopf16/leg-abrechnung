"""Shared offboarding-tracker edit dialog -- mirrors
`app.gui.onboarding_form` for the reverse process (see
`app.models.person_offboarding`).

The one thing this dialog does that onboarding's never needs: setting the
"Austrittsdatum MeteringPoint festgelegt" step offers, as an explicit,
separately-confirmed action, to end the person's currently open-ended
`Zuordnung`(en) with that date -- reusing the exact mechanism already used
for an ordinary mid-quarter tenant change (`app.models.zuordnung`). Never
automatic: saving the tracker's date alone does not touch any Zuordnung.
"""

from datetime import date, datetime
from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.safe_notify import safe_notify
from app.models import metering_point as metering_point_repo
from app.models import person_offboarding as person_offboarding_repo
from app.models import zuordnung as zuordnung_repo
from app.models.person import Person
from app.models.person_offboarding import GRUND_OPTIONS, STEPS, PersonOffboarding


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


def open_offboarding_form(
    offboarding: PersonOffboarding,
    person: Person,
    *,
    on_saved: Optional[Callable[[PersonOffboarding], None]] = None,
) -> None:
    """Open the edit dialog for one person's offboarding tracker.

    Args:
        offboarding: Tracker to edit (must already exist -- this dialog
            never creates one, see `person_offboarding.start_for_person`).
        person: The tracked person, for display and the dialog title.
        on_saved: Called with the updated `PersonOffboarding` after a
            successful save (dialog already closed).

    Returns:
        None.
    """
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
        ui.label(f"Austritt/Ausschluss: {person.anzeige_name}").classes("text-lg font-bold")
        ui.label(f"Grund: {GRUND_OPTIONS.get(offboarding.grund, offboarding.grund)}").classes(
            "text-caption text-grey-6"
        )
        ui.label(
            "Datum je Schritt eintragen, sobald er erledigt ist. Der offene "
            "Saldo dieser Person bleibt unabhängig davon bestehen."
        ).classes("text-caption text-grey-6")

        date_inputs: dict[str, ui.input] = {}
        for attr, label in STEPS:
            value = getattr(offboarding, attr)
            with ui.row().classes("w-full items-center gap-2"):
                date_inputs[attr] = ui.input(
                    label, value=value.isoformat() if value else ""
                ).props("type=date").classes("flex-grow")
                if attr == "metering_point_exit_at":
                    ui.button(
                        "Zuordnung(en) beenden",
                        on_click=lambda: open_end_zuordnung_dialog(
                            person, date_inputs["metering_point_exit_at"]
                        ),
                    ).props("dense outline")

        error_label = ui.label("").classes("text-negative")

        def save() -> None:
            """Validate the form and persist the offboarding tracker.

            Returns:
                None.
            """
            try:
                parsed = {attr: _parse_date(date_inputs[attr].value) for attr, _ in STEPS}
            except ValueError:
                error_label.text = "Ungültiges Datum."
                return

            for attr, _ in STEPS:
                setattr(offboarding, attr, parsed[attr])

            with connection_scope() as connection:
                person_offboarding_repo.update(connection, offboarding)
            dialog.close()
            safe_notify("Gespeichert.", type="positive")
            if on_saved:
                on_saved(offboarding)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            ui.button("Speichern", on_click=save)
    dialog.open()


def open_end_zuordnung_dialog(person: Person, date_input: ui.input) -> None:
    """Offer to end a person's currently open-ended Zuordnung(en).

    A separate, explicitly-confirmed action -- never triggered just by
    saving the offboarding tracker's date fields.

    Args:
        person: The person whose Zuordnungen to consider.
        date_input: The "Austrittsdatum MeteringPoint festgelegt" field --
            read at confirm time, so a date typed but not yet saved on
            the tracker can still be used here.

    Returns:
        None.
    """
    try:
        exit_date = _parse_date(date_input.value)
    except ValueError:
        exit_date = None
    if exit_date is None:
        safe_notify("Bitte zuerst ein Austrittsdatum eintragen.", type="warning")
        return

    with connection_scope() as connection:
        open_zuordnungen = [
            z for z in zuordnung_repo.list_for_person(connection, person.id) if z.gueltig_bis is None
        ]
        metering_point_names = {}
        for z in open_zuordnungen:
            metering_point = metering_point_repo.get(connection, z.metering_point_id)
            metering_point_names[z.id] = metering_point.designation if metering_point else f"Messpunkt #{z.metering_point_id}"
    if not open_zuordnungen:
        safe_notify("Keine offene Zuordnung für diese Person gefunden.", type="warning")
        return

    with ui.dialog() as confirm, ui.card():
        ui.label(f"Zuordnung(en) von {person.anzeige_name} per {exit_date.isoformat()} beenden?").classes(
            "font-bold"
        )
        for z in open_zuordnungen:
            ui.label(f"- {metering_point_names[z.id]}")
        ui.label(
            "Die nächste Quartalsabrechnung rechnet den Zeitraum bis zu "
            "diesem Datum automatisch anteilig ab."
        ).classes("text-caption text-grey-7")

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Abbrechen", on_click=confirm.close).props("flat")

            def do_end() -> None:
                with connection_scope() as connection:
                    for z in open_zuordnungen:
                        z.gueltig_bis = exit_date
                        zuordnung_repo.update(connection, z)
                confirm.close()
                safe_notify(f"{len(open_zuordnungen)} Zuordnung(en) beendet.", type="positive")

            ui.button("Beenden", on_click=do_end, color="negative")
    confirm.open()
