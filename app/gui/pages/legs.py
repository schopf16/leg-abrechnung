"""LEGs management page: list, search, create, edit, delete.

A LEG cannot be deleted while Standorte still reference it (see
`app.models.leg.LegInUseError`). Its `name` must be unique -- by default
it matches the physical Trafokreis it corresponds to, but two Trafokreise
can share one custom-named LEG if their owners agree to bill jointly.
The name is also what appears on this LEG's invoices, checked live as the
administrator types.
"""

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.models import leg as leg_repo
from app.models.leg import Leg, LegInUseError

COLUMNS = [
    {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
    {"name": "gemeinde", "label": "Gemeinde", "field": "gemeinde", "align": "left"},
    {"name": "standorte_count", "label": "Standorte", "field": "standorte_count", "align": "right"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]


def _to_row(connection, leg: Leg) -> dict:
    """Convert a `Leg` into a row dict for the NiceGUI table.

    Args:
        connection: Open SQLite connection.
        leg: LEG to convert.

    Returns:
        A dict with the fields required by `COLUMNS`, plus a hidden
        `_search` key used for client-side filtering.
    """
    search_text = " ".join([leg.name, leg.gemeinde or "", leg.bemerkung or ""]).lower()
    return {
        "id": leg.id,
        "name": leg.name,
        "gemeinde": leg.gemeinde,
        "standorte_count": leg_repo.count_standorte(connection, leg.id),
        "_search": search_text,
    }


@ui.page("/legs")
def legs_page() -> None:
    """Render the LEGs CRUD page with search.

    Returns:
        None.
    """
    with page_frame("/legs", "LEGs"):
        ui.label(
            "Eine LEG ist eine Eigenschaft des Standorts, nie einer Person "
            "oder eines Messpunkts direkt. Lokale Verteilung findet nur "
            "innerhalb derselben LEG statt (siehe Abrechnung). Der Name "
            "erscheint auf den Rechnungen dieser LEG."
        ).classes("text-body2 text-grey-8")

        search_input = ui.input("Suche (Name, Gemeinde, Bemerkung...)").classes(
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
            """Reload all LEGs from the database and re-apply the filter.

            Returns:
                None.
            """
            nonlocal all_rows
            with connection_scope() as connection:
                all_rows = [_to_row(connection, leg) for leg in leg_repo.list_all(connection)]
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())

        def open_form(existing: Leg | None) -> None:
            """Open the create/edit dialog for a LEG.

            Args:
                existing: LEG to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("LEG bearbeiten" if existing else "Neue LEG").classes(
                    "text-lg font-bold"
                )
                name = ui.input(
                    "Name (Trafokreis-Bezeichnung oder eigener LEG-Name)",
                    value=existing.name if existing else "",
                ).classes("w-full").props("debounce=300")
                duplicate_warning = ui.label("").classes("text-warning")
                gemeinde = ui.input(
                    "Gemeinde", value=existing.gemeinde if existing else ""
                ).classes("w-full")
                bemerkung = ui.textarea(
                    "Bemerkung (optional)",
                    value=existing.bemerkung if existing else "",
                ).classes("w-full").props("rows=3")
                error_label = ui.label("").classes("text-negative")

                def check_duplicate() -> bool:
                    """Check whether the current name input is already used by another LEG.

                    Updates `duplicate_warning` as a side effect.

                    Returns:
                        `True` if the name is a duplicate of a different LEG.
                    """
                    typed = name.value.strip()
                    if not typed:
                        duplicate_warning.text = ""
                        return False
                    with connection_scope() as connection:
                        found = leg_repo.get_by_name(connection, typed)
                    is_duplicate = found is not None and (existing is None or found.id != existing.id)
                    duplicate_warning.text = (
                        "Dieser Name wird bereits verwendet." if is_duplicate else ""
                    )
                    return is_duplicate

                name.on_value_change(lambda _: check_duplicate())

                def save() -> None:
                    """Validate the form and persist the LEG.

                    Returns:
                        None.
                    """
                    if not name.value.strip():
                        error_label.text = "Name darf nicht leer sein."
                        return
                    if not gemeinde.value.strip():
                        error_label.text = "Gemeinde darf nicht leer sein."
                        return
                    if check_duplicate():
                        error_label.text = "Dieser Name wird bereits verwendet."
                        return
                    try:
                        with connection_scope() as connection:
                            if existing:
                                updated = Leg(
                                    id=existing.id,
                                    name=name.value.strip(),
                                    gemeinde=gemeinde.value.strip(),
                                    bemerkung=bemerkung.value.strip(),
                                    created_at=existing.created_at,
                                )
                                leg_repo.update(connection, updated)
                            else:
                                new_leg = Leg(
                                    id=None,
                                    name=name.value.strip(),
                                    gemeinde=gemeinde.value.strip(),
                                    bemerkung=bemerkung.value.strip(),
                                    created_at="",
                                )
                                leg_repo.create(connection, new_leg)
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
                existing = leg_repo.get(connection, event.args["id"])
            open_form(existing)

        def on_remove(event) -> None:
            """Table row-delete handler: delete the LEG after confirmation.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            leg_id = event.args["id"]
            name = event.args["name"]

            with ui.dialog() as confirm, ui.card():
                ui.label(f'LEG "{name}" wirklich löschen?')
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        try:
                            with connection_scope() as connection:
                                leg_repo.delete(connection, leg_id)
                        except LegInUseError as exc:
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

        ui.button("+ Neue LEG", on_click=lambda: open_form(None)).classes("mt-2")

        refresh()
