"""Messpunkt-to-Person assignment history page (Zuordnungen)."""

from datetime import date, datetime
from typing import Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import zuordnung as zuordnung_repo
from app.models.zuordnung import Zuordnung

COLUMNS = [
    {"name": "messpunkt_label", "label": "Messpunkt", "field": "messpunkt_label", "align": "left", "sortable": True},
    {"name": "person_name", "label": "Person", "field": "person_name", "align": "left"},
    {"name": "gueltig_von", "label": "Gültig von", "field": "gueltig_von", "align": "left", "sortable": True},
    {"name": "gueltig_bis", "label": "Gültig bis", "field": "gueltig_bis", "align": "left"},
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
    """Render the Zuordnungen CRUD page, including consistency warnings.

    Returns:
        None.
    """
    with page_frame("/zuordnungen", "Zuordnungen"):
        ui.label(
            "Legt fest, welcher Person ein Messpunkt in welchem Zeitraum "
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
            """Reload the Zuordnungen table and recompute consistency warnings.

            Returns:
                None.
            """
            with connection_scope() as connection:
                messpunkte = {mp.id: mp for mp in messpunkt_repo.list_all(connection)}
                persons = {p.id: p for p in person_repo.list_all(connection)}
                zuordnungen = zuordnung_repo.list_all(connection)
                all_warnings = []
                for messpunkt_id in messpunkte:
                    all_warnings.extend(zuordnung_repo.find_warnings(connection, messpunkt_id))

            rows = [
                {
                    "id": z.id,
                    "messpunkt_id": z.messpunkt_id,
                    "person_id": z.person_id,
                    "messpunkt_label": messpunkte[z.messpunkt_id].messpunkt_bezeichnung
                    if z.messpunkt_id in messpunkte
                    else "?",
                    "person_name": persons[z.person_id].name if z.person_id in persons else "?",
                    "gueltig_von": z.gueltig_von.isoformat(),
                    "gueltig_bis": z.gueltig_bis.isoformat() if z.gueltig_bis else "offen",
                }
                for z in zuordnungen
            ]
            table.rows = rows
            table.update()

            warnings_column.clear()
            with warnings_column:
                for warning in all_warnings:
                    ui.label(f"⚠ {warning.message}").classes(
                        "text-negative text-body2"
                    )

        def open_form(existing: Optional[Zuordnung]) -> None:
            """Open the create/edit dialog for a Zuordnung.

            Args:
                existing: Zuordnung to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with connection_scope() as connection:
                messpunkte = messpunkt_repo.list_all(connection)
                persons = person_repo.list_all(connection)
            messpunkt_options = {
                mp.id: f"{mp.messpunkt_bezeichnung} ({'Bezug' if mp.is_bezug else 'Einspeisung'})"
                for mp in messpunkte
            }
            person_options = {p.id: p.name for p in persons}

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label(
                    "Zuordnung bearbeiten" if existing else "Neue Zuordnung"
                ).classes("text-lg font-bold")
                messpunkt_select = ui.select(
                    messpunkt_options,
                    label="Messpunkt",
                    value=existing.messpunkt_id if existing else (messpunkte[0].id if messpunkte else None),
                ).classes("w-full")
                person_select = ui.select(
                    person_options,
                    label="Person",
                    value=existing.person_id
                    if existing
                    else (persons[0].id if persons else None),
                ).classes("w-full")
                gueltig_von = ui.input(
                    "Gültig von",
                    value=existing.gueltig_von.isoformat() if existing else date.today().isoformat(),
                ).props("type=date").classes("w-full")
                gueltig_bis = ui.input(
                    "Gültig bis (leer = offen)",
                    value=existing.gueltig_bis.isoformat() if existing and existing.gueltig_bis else "",
                ).props("type=date").classes("w-full")
                error_label = ui.label("").classes("text-negative")

                def save() -> None:
                    """Validate the form and persist the Zuordnung.

                    Returns:
                        None.
                    """
                    if messpunkt_select.value is None or person_select.value is None:
                        error_label.text = "Messpunkt und Person sind erforderlich."
                        return
                    try:
                        from_date = _parse_date(gueltig_von.value)
                        to_date = _parse_date(gueltig_bis.value)
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
                            updated = Zuordnung(
                                id=existing.id,
                                person_id=person_select.value,
                                messpunkt_id=messpunkt_select.value,
                                gueltig_von=from_date,
                                gueltig_bis=to_date,
                                created_at=existing.created_at,
                            )
                            zuordnung_repo.update(connection, updated)
                        else:
                            new_zuordnung = Zuordnung(
                                id=None,
                                person_id=person_select.value,
                                messpunkt_id=messpunkt_select.value,
                                gueltig_von=from_date,
                                gueltig_bis=to_date,
                                created_at="",
                            )
                            zuordnung_repo.create(connection, new_zuordnung)
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
                zuordnungen = {z.id: z for z in zuordnung_repo.list_all(connection)}
            existing = zuordnungen.get(event.args["id"])
            if existing:
                open_form(existing)

        def on_remove(event) -> None:
            """Table row-delete handler: delete the Zuordnung after confirmation.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            zuordnung_id = event.args["id"]

            with ui.dialog() as confirm, ui.card():
                ui.label("Diese Zuordnung wirklich löschen?")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            zuordnung_repo.delete(connection, zuordnung_id)
                        confirm.close()
                        refresh()
                        ui.notify("Gelöscht.", type="warning")

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("edit", on_edit)
        table.on("remove", on_remove)

        ui.button("+ Neue Zuordnung", on_click=lambda: open_form(None)).classes("mt-2")

        refresh()
