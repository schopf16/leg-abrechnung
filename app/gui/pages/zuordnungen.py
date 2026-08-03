"""Meter-to-participant assignment history page (Zuordnungen)."""

from datetime import date, datetime
from typing import Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.models import assignment as assignment_repo
from app.models import meter as meter_repo
from app.models import participant as participant_repo
from app.models.assignment import MeterAssignment

COLUMNS = [
    {"name": "meter_label", "label": "Zähler", "field": "meter_label", "align": "left", "sortable": True},
    {"name": "participant_name", "label": "Teilnehmer", "field": "participant_name", "align": "left"},
    {"name": "valid_from", "label": "Gültig von", "field": "valid_from", "align": "left", "sortable": True},
    {"name": "valid_to", "label": "Gültig bis", "field": "valid_to", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]


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


@ui.page("/zuordnungen")
def zuordnungen_page() -> None:
    """Render the meter-assignment CRUD page, including consistency warnings.

    Returns:
        None.
    """
    with page_frame("/zuordnungen", "Zuordnungen"):
        ui.label(
            "Legt fest, welchem Teilnehmer ein Zähler in welchem Zeitraum "
            "zugeordnet ist. Bei einem Umzug mitten im Quartal zwei "
            "Zuordnungen mit passendem Enddatum/Startdatum anlegen."
        ).classes("text-body2 text-grey-8")

        warnings_column = ui.column().classes("w-full")
        table = ui.table(columns=COLUMNS, rows=[], row_key="id").classes("w-full")
        table.add_slot(
            "body-cell-actions",
            r'''
            <q-td :props="props">
                <q-btn dense flat icon="edit" @click="() => $parent.$emit('edit', props.row)" />
                <q-btn dense flat icon="delete" color="negative" @click="() => $parent.$emit('remove', props.row)" />
            </q-td>
            ''',
        )

        def refresh() -> None:
            """Reload the assignments table and recompute consistency warnings.

            Returns:
                None.
            """
            with connection_scope() as connection:
                meters = {m.id: m for m in meter_repo.list_all(connection)}
                participants = {p.id: p for p in participant_repo.list_all(connection)}
                assignments = assignment_repo.list_all(connection)
                all_warnings = []
                for meter_id in meters:
                    all_warnings.extend(assignment_repo.find_warnings(connection, meter_id))

            rows = [
                {
                    "id": a.id,
                    "meter_id": a.meter_id,
                    "participant_id": a.participant_id,
                    "meter_label": meters[a.meter_id].label if a.meter_id in meters else "?",
                    "participant_name": participants[a.participant_id].name
                    if a.participant_id in participants
                    else "?",
                    "valid_from": a.valid_from.isoformat(),
                    "valid_to": a.valid_to.isoformat() if a.valid_to else "offen",
                }
                for a in assignments
            ]
            table.rows = rows
            table.update()

            warnings_column.clear()
            with warnings_column:
                for warning in all_warnings:
                    ui.label(f"⚠ {warning.message}").classes(
                        "text-negative text-body2"
                    )

        def open_form(existing: Optional[MeterAssignment]) -> None:
            """Open the create/edit dialog for a meter assignment.

            Args:
                existing: Assignment to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with connection_scope() as connection:
                meters = meter_repo.list_all(connection)
                participants = participant_repo.list_all(connection)
            meter_options = {m.id: f"{m.label} ({m.metering_point_id})" for m in meters}
            participant_options = {p.id: p.name for p in participants}

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label(
                    "Zuordnung bearbeiten" if existing else "Neue Zuordnung"
                ).classes("text-lg font-bold")
                meter_select = ui.select(
                    meter_options,
                    label="Zähler",
                    value=existing.meter_id if existing else (meters[0].id if meters else None),
                ).classes("w-full")
                participant_select = ui.select(
                    participant_options,
                    label="Teilnehmer",
                    value=existing.participant_id
                    if existing
                    else (participants[0].id if participants else None),
                ).classes("w-full")
                valid_from = ui.input(
                    "Gültig von",
                    value=existing.valid_from.isoformat() if existing else date.today().isoformat(),
                ).props("type=date").classes("w-full")
                valid_to = ui.input(
                    "Gültig bis (leer = offen)",
                    value=existing.valid_to.isoformat() if existing and existing.valid_to else "",
                ).props("type=date").classes("w-full")
                error_label = ui.label("").classes("text-negative")

                def save() -> None:
                    """Validate the form and persist the assignment.

                    Returns:
                        None.
                    """
                    if meter_select.value is None or participant_select.value is None:
                        error_label.text = "Zähler und Teilnehmer sind erforderlich."
                        return
                    try:
                        from_date = _parse_date(valid_from.value)
                        to_date = _parse_date(valid_to.value)
                    except ValueError:
                        error_label.text = "Ungültiges Datum."
                        return
                    if from_date is None:
                        error_label.text = "„Gültig von“ ist erforderlich."
                        return
                    if to_date is not None and to_date < from_date:
                        error_label.text = "„Gültig bis“ darf nicht vor „Gültig von“ liegen."
                        return

                    with connection_scope() as connection:
                        if existing:
                            updated = MeterAssignment(
                                id=existing.id,
                                meter_id=meter_select.value,
                                participant_id=participant_select.value,
                                valid_from=from_date,
                                valid_to=to_date,
                                created_at=existing.created_at,
                            )
                            assignment_repo.update(connection, updated)
                        else:
                            new_assignment = MeterAssignment(
                                id=None,
                                meter_id=meter_select.value,
                                participant_id=participant_select.value,
                                valid_from=from_date,
                                valid_to=to_date,
                                created_at="",
                            )
                            assignment_repo.create(connection, new_assignment)
                    dialog.close()
                    refresh()
                    ui.notify("Gespeichert.", type="positive")

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    ui.button("Speichern", on_click=save)
            dialog.open()

        def on_edit(event) -> None:
            """Table row-edit handler: open the edit dialog for the clicked row.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            with connection_scope() as connection:
                assignments = {a.id: a for a in assignment_repo.list_all(connection)}
            existing = assignments.get(event.args["id"])
            if existing:
                open_form(existing)

        def on_remove(event) -> None:
            """Table row-delete handler: delete the assignment after confirmation.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            assignment_id = event.args["id"]

            with ui.dialog() as confirm, ui.card():
                ui.label("Diese Zuordnung wirklich löschen?")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            assignment_repo.delete(connection, assignment_id)
                        confirm.close()
                        refresh()
                        ui.notify("Gelöscht.", type="warning")

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("edit", on_edit)
        table.on("remove", on_remove)

        ui.button("+ Neue Zuordnung", on_click=lambda: open_form(None)).classes("mt-2")

        refresh()
