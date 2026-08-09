"""Trafokreise management page: list, search, create, edit, delete.

A Trafokreis cannot be deleted while Standorte still reference it (see
`app.models.trafokreis.TrafokreisInUseError`). Its `name` must be unique,
checked live as the administrator types.
"""

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.models import trafokreis as trafokreis_repo
from app.models.trafokreis import Trafokreis, TrafokreisInUseError

COLUMNS = [
    {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
    {"name": "bkw_bezeichnung", "label": "BKW-Bezeichnung", "field": "bkw_bezeichnung", "align": "left"},
    {"name": "standorte_count", "label": "Standorte", "field": "standorte_count", "align": "right"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]


def _to_row(connection, trafokreis: Trafokreis) -> dict:
    """Convert a `Trafokreis` into a row dict for the NiceGUI table.

    Args:
        connection: Open SQLite connection.
        trafokreis: Trafokreis to convert.

    Returns:
        A dict with the fields required by `COLUMNS`, plus a hidden
        `_search` key used for client-side filtering.
    """
    search_text = " ".join(
        [trafokreis.name, trafokreis.bkw_bezeichnung or "", trafokreis.bemerkung or ""]
    ).lower()
    return {
        "id": trafokreis.id,
        "name": trafokreis.name,
        "bkw_bezeichnung": trafokreis.bkw_bezeichnung,
        "standorte_count": trafokreis_repo.count_standorte(connection, trafokreis.id),
        "_search": search_text,
    }


@ui.page("/trafokreise")
def trafokreise_page() -> None:
    """Render the Trafokreise CRUD page with search.

    Returns:
        None.
    """
    with page_frame("/trafokreise", "Trafokreise"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Ein Trafokreis ist eine Eigenschaft des Standorts, nie einer "
                "Person, eines Messpunkts oder einer LEG direkt. Er entspricht "
                "der physischen Gruppierung durch den Netzbetreiber (BKW). Der "
                "„Name“ ist ein frei wählbarer (Pseudo-)Name; die offizielle "
                "BKW-Nummer gehört ins Feld „BKW-Bezeichnung“."
            ).classes("text-body2 text-grey-8")
            ui.button("+ Neuer Trafokreis", on_click=lambda: open_form(None)).classes("shrink-0")

        search_input = ui.input("Suche (Name, BKW-Bezeichnung, Bemerkung...)").classes(
            "w-full max-w-md"
        ).props("debounce=300 clearable")

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

        all_rows: list[dict] = []

        def apply_filter() -> None:
            """Filter the currently loaded rows by the search input's value.

            Returns:
                None.
            """
            needle = (search_input.value or "").strip().lower()
            table.rows = [r for r in all_rows if needle in r["_search"]] if needle else list(all_rows)
            table.update()

        def refresh() -> None:
            """Reload all Trafokreise from the database and re-apply the filter.

            Returns:
                None.
            """
            nonlocal all_rows
            with connection_scope() as connection:
                all_rows = [
                    _to_row(connection, trafokreis)
                    for trafokreis in trafokreis_repo.list_all(connection)
                ]
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())

        def open_form(existing: Trafokreis | None) -> None:
            """Open the create/edit dialog for a Trafokreis.

            Args:
                existing: Trafokreis to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Trafokreis bearbeiten" if existing else "Neuer Trafokreis").classes(
                    "text-lg font-bold"
                )
                name = ui.input(
                    "Name (frei wählbar, z. B. Pseudo-Name)",
                    value=existing.name if existing else "",
                ).classes("w-full").props("debounce=300")
                duplicate_warning = ui.label("").classes("text-warning")
                bkw_bezeichnung = ui.input(
                    "BKW-Bezeichnung (optional, z. B. TRA21359)",
                    value=existing.bkw_bezeichnung if existing else "",
                ).classes("w-full")
                bemerkung = ui.textarea(
                    "Bemerkung (optional)",
                    value=existing.bemerkung if existing else "",
                ).classes("w-full").props("rows=3")
                error_label = ui.label("").classes("text-negative")

                def check_duplicate() -> bool:
                    """Check whether the current name input is already used by another Trafokreis.

                    Updates `duplicate_warning` as a side effect.

                    Returns:
                        `True` if the name is a duplicate of a different Trafokreis.
                    """
                    typed = name.value.strip()
                    if not typed:
                        duplicate_warning.text = ""
                        return False
                    with connection_scope() as connection:
                        found = trafokreis_repo.get_by_name(connection, typed)
                    is_duplicate = found is not None and (existing is None or found.id != existing.id)
                    duplicate_warning.text = (
                        "Dieser Name wird bereits verwendet." if is_duplicate else ""
                    )
                    return is_duplicate

                name.on_value_change(lambda _: check_duplicate())

                def save() -> None:
                    """Validate the form and persist the Trafokreis.

                    Returns:
                        None.
                    """
                    if not name.value.strip():
                        error_label.text = "Name darf nicht leer sein."
                        return
                    if check_duplicate():
                        error_label.text = "Dieser Name wird bereits verwendet."
                        return
                    try:
                        with connection_scope() as connection:
                            if existing:
                                updated = Trafokreis(
                                    id=existing.id,
                                    name=name.value.strip(),
                                    bkw_bezeichnung=bkw_bezeichnung.value.strip(),
                                    bemerkung=bemerkung.value.strip(),
                                    created_at=existing.created_at,
                                )
                                trafokreis_repo.update(connection, updated)
                            else:
                                new_trafokreis = Trafokreis(
                                    id=None,
                                    name=name.value.strip(),
                                    bkw_bezeichnung=bkw_bezeichnung.value.strip(),
                                    bemerkung=bemerkung.value.strip(),
                                    created_at="",
                                )
                                trafokreis_repo.create(connection, new_trafokreis)
                    except Exception as exc:  # unique constraint race, etc.
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
                existing = trafokreis_repo.get(connection, event.args["id"])
            open_form(existing)

        def on_remove(event) -> None:
            """Table row-delete handler: delete the Trafokreis after confirmation.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            trafokreis_id = event.args["id"]
            name = event.args["name"]

            with ui.dialog() as confirm, ui.card():
                ui.label(f'Trafokreis "{name}" wirklich löschen?')
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        try:
                            with connection_scope() as connection:
                                trafokreis_repo.delete(connection, trafokreis_id)
                        except TrafokreisInUseError as exc:
                            confirm.close()
                            ui.notify(str(exc), type="negative")
                            return
                        confirm.close()
                        refresh()
                        ui.notify("Gelöscht.", type="warning")

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("edit", on_edit)
        table.on("remove", on_remove)

        refresh()
