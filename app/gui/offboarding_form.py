"""Shared offboarding-tracker edit dialog -- mirrors `app.gui.onboarding_form` for the reverse process
(see `app.models.person_offboarding`)."""

from datetime import date, datetime
from typing import Callable, Optional

from nicegui import ui

from app.formatting import format_date
from app.db.connection import connection_scope
from app.gui.form_dialog import form_guard
from app.gui.safe_notify import safe_notify
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import person_offboarding as person_offboarding_repo
from app.models import assignment as assignment_repo
from app.models.person import Person
from app.models.person_offboarding import REASON_OPTIONS, STEPS, PersonOffboarding


def _parse_date(value: str) -> Optional[date]:
    """Parse a date string from a NiceGUI date input into a `date`."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def open_offboarding_form(
    offboarding: PersonOffboarding,
    person: Person,
    *,
    on_saved: Optional[Callable[[PersonOffboarding], None]] = None,
) -> None:
    """Open the edit dialog for one person's offboarding tracker."""
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
        ui.label(f"Austritt/Ausschluss: {person.display_name}").classes("text-lg font-bold")
        ui.label(f"Grund: {REASON_OPTIONS.get(offboarding.reason, offboarding.reason)}").classes(
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
                date_inputs[attr] = (
                    ui.input(label, value=value.isoformat() if value else "")
                    .props("type=date")
                    .classes("flex-grow")
                )
                if attr == "metering_point_exit_at":
                    ui.button(
                        "Zuordnung(en) beenden",
                        on_click=lambda: open_end_assignment_dialog(
                            person, date_inputs["metering_point_exit_at"]
                        ),
                    ).props("dense outline")
                if attr == STEPS[-1][0]:
                    ui.button(
                        "Person entfernen",
                        on_click=lambda: open_remove_person_dialog(person, on_done=on_saved_refresh),
                    ).props("dense outline color=negative")

        error_label = ui.label("").classes("text-negative")

        def on_saved_refresh() -> None:
            """Let the calling page refresh after the person was removed."""
            if on_saved:
                on_saved(offboarding)

        def save() -> None:
            """Validate the form and persist the offboarding tracker."""
            try:
                parsed = {attr: _parse_date(date_inputs[attr].value) for attr, _ in STEPS}
            except ValueError:
                error_label.text = "Ungültiges Datum."
                return

            was_complete = offboarding.is_complete
            for attr, _ in STEPS:
                setattr(offboarding, attr, parsed[attr])

            with connection_scope() as connection:
                person_offboarding_repo.update(connection, offboarding)
                still_active = (person_repo.get(connection, person.id) or person).active
            dialog.close()
            safe_notify("Gespeichert.", type="positive")
            if on_saved:
                on_saved(offboarding)
            # The moment the process actually finishes is the moment to ask
            # -- otherwise the person silently stays active everywhere.
            if offboarding.is_complete and not was_complete and still_active:
                open_remove_person_dialog(person, on_done=on_saved_refresh)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            ui.button("Speichern", on_click=save)
    form_guard(dialog, on_save=save)
    dialog.open()


def open_end_assignment_dialog(person: Person, date_input: ui.input) -> None:
    """Offer to end a person's currently open-ended Assignment(en)."""
    try:
        exit_date = _parse_date(date_input.value)
    except ValueError:
        exit_date = None
    if exit_date is None:
        safe_notify("Bitte zuerst ein Austrittsdatum eintragen.", type="warning")
        return

    with connection_scope() as connection:
        open_assignments = [
            z for z in assignment_repo.list_for_person(connection, person.id) if z.valid_to is None
        ]
        metering_point_names = {}
        for z in open_assignments:
            metering_point = metering_point_repo.get(connection, z.metering_point_id)
            metering_point_names[z.id] = (
                metering_point.designation if metering_point else f"Messpunkt #{z.metering_point_id}"
            )
    if not open_assignments:
        safe_notify("Keine offene Zuordnung für diese Person gefunden.", type="warning")
        return

    with ui.dialog() as confirm, ui.card():
        ui.label(f"Zuordnung(en) von {person.display_name} per {format_date(exit_date)} beenden?").classes(
            "font-bold"
        )
        for z in open_assignments:
            ui.label(f"- {metering_point_names[z.id]}")
        ui.label(
            "Die nächste Quartalsabrechnung rechnet den Zeitraum bis zu diesem Datum automatisch anteilig ab."
        ).classes("text-caption text-grey-7")

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Abbrechen", on_click=confirm.close).props("flat")

            def do_end() -> None:
                with connection_scope() as connection:
                    for z in open_assignments:
                        z.valid_to = exit_date
                        assignment_repo.update(connection, z)
                confirm.close()
                safe_notify(f"{len(open_assignments)} Zuordnung(en) beendet.", type="positive")

            ui.button("Beenden", on_click=do_end, color="negative")
    confirm.open()


def open_remove_person_dialog(person: Person, *, on_done: Optional[Callable[[], None]] = None) -> None:
    """Offer to remove a person once their offboarding is finished."""
    with ui.dialog() as confirm, ui.card().classes("w-full max-w-md"):
        ui.label(f"Austritt abgeschlossen -- „{person.display_name}“ entfernen?").classes("font-bold")
        ui.label(
            "Bestehen bereits Abrechnungen für diese Person, wird sie nur "
            "deaktiviert (Kundennummer und Buchhaltung bleiben erhalten), "
            "sonst wird sie ganz gelöscht. In beiden Fällen zählt sie nicht "
            "mehr in der Übersicht, ist bei neuen Zuordnungen nicht mehr "
            "wählbar und erhält keine Rundmails mehr."
        ).classes("text-caption text-grey-7")
        ui.label(
            "Standort und Messpunkte bleiben bestehen -- sie sind physisch "
            "vorhanden und können neu zugeordnet werden. Wird ein Standort "
            "nicht mehr gebraucht, löschen Sie ihn separat unter „Standorte“."
        ).classes("text-caption text-grey-7")

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Behalten", on_click=confirm.close).props("flat")

            def do_remove() -> None:
                """Delete or deactivate the person, then report what happened."""
                with connection_scope() as connection:
                    deleted = person_repo.delete(connection, person.id)
                confirm.close()
                safe_notify(
                    "Person gelöscht."
                    if deleted
                    else "Person deaktiviert (Abrechnungen vorhanden, Historie bleibt erhalten).",
                    type="positive",
                )
                if on_done:
                    on_done()

            ui.button("Entfernen", on_click=do_remove, color="negative")
    confirm.open()
