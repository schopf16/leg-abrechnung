"""Meters (Zähler) management page: list, create, edit, delete."""

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.models import meter as meter_repo
from app.models.meter import Meter

ROLE_LABELS = {
    "bezug": "Bezug",
    "produktion": "Produktion",
    "bezug_fix": "Bezug (fix)",
    "bezug_geschaltet": "Bezug (geschaltet)",
}

COLUMNS = [
    {"name": "metering_point_id", "label": "Zählpunkt-ID", "field": "metering_point_id", "align": "left", "sortable": True},
    {"name": "label", "label": "Bezeichnung", "field": "label", "align": "left"},
    {"name": "building_address", "label": "Wohnhaus / Adresse", "field": "building_address", "align": "left"},
    {"name": "role_label", "label": "Rolle", "field": "role_label", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]


def _to_row(m: Meter) -> dict:
    """Convert a `Meter` into a row dict for the NiceGUI table.

    Args:
        m: Meter to convert.

    Returns:
        A dict with the fields required by `COLUMNS`.
    """
    return {
        "id": m.id,
        "metering_point_id": m.metering_point_id,
        "label": m.label,
        "building_address": m.building_address,
        "role_label": ROLE_LABELS.get(m.role, m.role),
    }


@ui.page("/zaehler")
def zaehler_page() -> None:
    """Render the meters CRUD page.

    Returns:
        None.
    """
    with page_frame("/zaehler", "Zähler"):
        ui.label(
            "Zähler sind fix pro Wohnhaus. Wer über einen Zähler abgerechnet "
            "wird, legen Sie unter „Zuordnungen“ fest."
        ).classes("text-body2 text-grey-8")

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
            """Reload the meters table from the database.

            Returns:
                None.
            """
            with connection_scope() as connection:
                rows = [_to_row(m) for m in meter_repo.list_all(connection)]
            table.rows = rows
            table.update()

        def open_form(existing: Meter | None) -> None:
            """Open the create/edit dialog for a meter.

            Args:
                existing: Meter to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Zähler bearbeiten" if existing else "Neuer Zähler").classes(
                    "text-lg font-bold"
                )
                metering_point_id = ui.input(
                    "Zählpunkt-ID (aus BKW-Daten)",
                    value=existing.metering_point_id if existing else "",
                ).classes("w-full")
                label = ui.input("Bezeichnung", value=existing.label if existing else "").classes(
                    "w-full"
                )
                building_address = ui.input(
                    "Wohnhaus / Adresse",
                    value=existing.building_address if existing else "",
                ).classes("w-full")
                role = ui.select(
                    ROLE_LABELS,
                    label="Rolle",
                    value=existing.role if existing else "bezug",
                ).classes("w-full")
                error_label = ui.label("").classes("text-negative")

                def save() -> None:
                    """Validate the form and persist the meter.

                    Returns:
                        None.
                    """
                    if not metering_point_id.value.strip():
                        error_label.text = "Zählpunkt-ID darf nicht leer sein."
                        return
                    if not label.value.strip():
                        error_label.text = "Bezeichnung darf nicht leer sein."
                        return
                    try:
                        with connection_scope() as connection:
                            if existing:
                                updated = Meter(
                                    id=existing.id,
                                    metering_point_id=metering_point_id.value.strip(),
                                    label=label.value.strip(),
                                    building_address=building_address.value.strip(),
                                    role=role.value,
                                    created_at=existing.created_at,
                                )
                                meter_repo.update(connection, updated)
                            else:
                                new_meter = Meter(
                                    id=None,
                                    metering_point_id=metering_point_id.value.strip(),
                                    label=label.value.strip(),
                                    building_address=building_address.value.strip(),
                                    role=role.value,
                                    created_at="",
                                )
                                meter_repo.create(connection, new_meter)
                    except Exception as exc:  # unique constraint, etc.
                        error_label.text = f"Fehler beim Speichern: {exc}"
                        return
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
                existing = meter_repo.get(connection, event.args["id"])
            open_form(existing)

        def on_remove(event) -> None:
            """Table row-delete handler: delete the meter after confirmation.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            meter_id = event.args["id"]
            label_text = event.args["label"]

            with ui.dialog() as confirm, ui.card():
                ui.label(
                    f'Zähler "{label_text}" wirklich löschen? '
                    "Zugehörige Zuordnungen und Messwerte werden mitgelöscht."
                )
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            meter_repo.delete(connection, meter_id)
                        confirm.close()
                        refresh()
                        ui.notify("Gelöscht.", type="warning")

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("edit", on_edit)
        table.on("remove", on_remove)

        ui.button("+ Neuer Zähler", on_click=lambda: open_form(None)).classes("mt-2")

        refresh()
