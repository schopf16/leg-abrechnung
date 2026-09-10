"""Signaturen page: list, create, edit, delete named email signatures
(see `app.models.signature`).

A signature is never applied automatically -- it is only ever added to an
outgoing email when explicitly chosen on the E-Mail-Versand page (see
`app.gui.pages.email_versand`). Deleting one here has no effect on
already-sent emails: their final text (signature included, if any) is
already part of the logged history, not a live reference back to this
table.
"""

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.gui.print_list import render_print_button, table_columns
from app.gui.safe_notify import safe_notify
from app.models import signature as signature_repo
from app.models.signature import Signature

COLUMNS = [
    {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
    {"name": "preview", "label": "Vorschau", "field": "preview", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]


def _to_row(signature: Signature) -> dict:
    """Convert a `Signature` into a row dict for the NiceGUI table.

    Args:
        signature: Signature to convert.

    Returns:
        A dict with the fields required by `COLUMNS`, plus a hidden
        `_search` key used for client-side filtering.
    """
    first_line = signature.content.strip().splitlines()[0] if signature.content.strip() else ""
    return {
        "id": signature.id,
        "name": signature.name,
        "preview": first_line[:60] + ("…" if len(first_line) > 60 else ""),
        "_search": f"{signature.name} {signature.content}".lower(),
    }


@ui.page("/signaturen")
def signaturen_page() -> None:
    """Render the Signaturen CRUD page with search.

    Returns:
        None.
    """
    with page_frame("/signaturen", "Signaturen"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Wiederverwendbare Signaturen für den E-Mail-Versand. Eine "
                "Signatur wird nie automatisch angehängt -- beim Versenden "
                "wird dort jeweils gewählt, ob und welche Signatur verwendet "
                "werden soll."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    rubrik="Signaturen",
                    get_columns=lambda: table_columns(table),
                    get_rows=lambda: table.rows,
                    get_filter_description=lambda: (
                        f'Suche: "{search_input.value.strip()}"' if search_input.value else None
                    ),
                )
                ui.button("+ Neue Signatur", on_click=lambda: open_form(None))

        search_input = ui.input("Suche (Name, Inhalt)").classes("w-full max-w-md").props(
            "debounce=300 clearable"
        )

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
            """Reload all signatures from the database and re-apply the filter.

            Returns:
                None.
            """
            nonlocal all_rows
            with connection_scope() as connection:
                signatures = signature_repo.list_all(connection)
            all_rows = [_to_row(s) for s in signatures]
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())

        def open_form(existing: Signature | None) -> None:
            """Open the create/edit dialog for a signature.

            Args:
                existing: Signature to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
                ui.label("Signatur bearbeiten" if existing else "Neue Signatur").classes(
                    "text-lg font-bold"
                )
                name = ui.input(
                    "Name (zur Auswahl beim Versenden)",
                    value=existing.name if existing else "",
                ).classes("w-full").props("debounce=300")
                duplicate_warning = ui.label("").classes("text-warning")
                content = ui.textarea(
                    "Inhalt", value=existing.content if existing else ""
                ).classes("w-full").props("rows=8")
                error_label = ui.label("").classes("text-negative")

                def check_duplicate() -> bool:
                    """Check whether the current name is already used by another signature.

                    Updates `duplicate_warning` as a side effect.

                    Returns:
                        `True` if the name is a duplicate of a different signature.
                    """
                    typed = name.value.strip()
                    if not typed:
                        duplicate_warning.text = ""
                        return False
                    with connection_scope() as connection:
                        found = signature_repo.get_by_name(connection, typed)
                    is_duplicate = found is not None and (existing is None or found.id != existing.id)
                    duplicate_warning.text = (
                        "Dieser Name wird bereits verwendet." if is_duplicate else ""
                    )
                    return is_duplicate

                name.on_value_change(lambda _: check_duplicate())

                def save() -> None:
                    """Validate the form and persist the signature.

                    Returns:
                        None.
                    """
                    if not name.value.strip():
                        error_label.text = "Name darf nicht leer sein."
                        return
                    if not content.value.strip():
                        error_label.text = "Inhalt darf nicht leer sein."
                        return
                    if check_duplicate():
                        error_label.text = "Dieser Name wird bereits verwendet."
                        return
                    try:
                        with connection_scope() as connection:
                            if existing:
                                updated = Signature(
                                    id=existing.id, name=name.value.strip(),
                                    content=content.value, created_at=existing.created_at,
                                )
                                signature_repo.update(connection, updated)
                            else:
                                new_signature = Signature(
                                    id=None, name=name.value.strip(),
                                    content=content.value, created_at="",
                                )
                                signature_repo.create(connection, new_signature)
                    except Exception as exc:  # unique constraint race, etc.
                        error_label.text = f"Fehler beim Speichern: {exc}"
                        return
                    dialog.close()
                    # notify before refresh() -- see app.gui.safe_notify's
                    # module docstring for why a plain ui.notify() here can
                    # raise "parent element ... has been deleted" once the
                    # dialog it was called from is gone.
                    safe_notify("Gespeichert.", type="positive")
                    refresh()

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
                existing = signature_repo.get(connection, event.args["id"])
            open_form(existing)

        def on_remove(event) -> None:
            """Table row-delete handler: delete the signature after confirmation.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            signature_id = event.args["id"]
            name = event.args["name"]

            with ui.dialog() as confirm, ui.card():
                ui.label(f'Signatur "{name}" wirklich löschen?')
                ui.label(
                    "Bereits versendete E-Mails sind davon nicht betroffen."
                ).classes("text-caption text-grey-7")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            signature_repo.delete(connection, signature_id)
                        confirm.close()
                        # notify before refresh() -- see save() above for why
                        safe_notify("Gelöscht.", type="warning")
                        refresh()

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("edit", on_edit)
        table.on("remove", on_remove)

        refresh()
