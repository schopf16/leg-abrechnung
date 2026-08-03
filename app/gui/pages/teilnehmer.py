"""Participants (Teilnehmer) management page: list, create, edit, delete."""

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.models import participant as participant_repo
from app.models.participant import Participant

COLUMNS = [
    {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
    {"name": "address", "label": "Adresse", "field": "address", "align": "left"},
    {"name": "iban", "label": "IBAN", "field": "iban", "align": "left"},
    {"name": "email", "label": "E-Mail", "field": "email", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]


def _to_row(p: Participant) -> dict:
    """Convert a `Participant` into a row dict for the NiceGUI table.

    Args:
        p: Participant to convert.

    Returns:
        A dict with the fields required by `COLUMNS`.
    """
    address = f"{p.address_street}, {p.address_zip} {p.address_city}".strip(", ")
    return {
        "id": p.id,
        "name": p.name,
        "address": address,
        "iban": p.iban,
        "email": p.email,
    }


@ui.page("/teilnehmer")
def teilnehmer_page() -> None:
    """Render the participants CRUD page.

    Returns:
        None.
    """
    with page_frame("/teilnehmer", "Teilnehmer"):
        ui.label(
            "Personen oder Firmen, die an der LEG teilnehmen (Bezüger, "
            "Produzenten oder beides)."
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
            """Reload the participants table from the database.

            Returns:
                None.
            """
            with connection_scope() as connection:
                rows = [_to_row(p) for p in participant_repo.list_all(connection)]
            table.rows = rows
            table.update()

        def open_form(existing: Participant | None) -> None:
            """Open the create/edit dialog for a participant.

            Args:
                existing: Participant to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Teilnehmer bearbeiten" if existing else "Neuer Teilnehmer").classes(
                    "text-lg font-bold"
                )
                name = ui.input("Name / Firma", value=existing.name if existing else "").classes("w-full")
                street = ui.input(
                    "Strasse", value=existing.address_street if existing else ""
                ).classes("w-full")
                with ui.row().classes("w-full gap-2"):
                    zip_code = ui.input(
                        "PLZ", value=existing.address_zip if existing else ""
                    ).classes("w-24")
                    city = ui.input(
                        "Ort", value=existing.address_city if existing else ""
                    ).classes("flex-grow")
                country = ui.input(
                    "Land", value=existing.address_country if existing else "CH"
                ).classes("w-full")
                iban = ui.input(
                    "IBAN (für Gutschriften)", value=existing.iban if existing else ""
                ).classes("w-full")
                email = ui.input(
                    "E-Mail (optional)", value=existing.email if existing else ""
                ).classes("w-full")
                error_label = ui.label("").classes("text-negative")

                def save() -> None:
                    """Validate the form and persist the participant.

                    Returns:
                        None.
                    """
                    if not name.value.strip():
                        error_label.text = "Name darf nicht leer sein."
                        return
                    with connection_scope() as connection:
                        if existing:
                            updated = Participant(
                                id=existing.id,
                                name=name.value.strip(),
                                address_street=street.value.strip(),
                                address_zip=zip_code.value.strip(),
                                address_city=city.value.strip(),
                                address_country=country.value.strip() or "CH",
                                iban=iban.value.strip(),
                                email=email.value.strip(),
                                created_at=existing.created_at,
                            )
                            participant_repo.update(connection, updated)
                        else:
                            new_participant = Participant(
                                id=None,
                                name=name.value.strip(),
                                address_street=street.value.strip(),
                                address_zip=zip_code.value.strip(),
                                address_city=city.value.strip(),
                                address_country=country.value.strip() or "CH",
                                iban=iban.value.strip(),
                                email=email.value.strip(),
                                created_at="",
                            )
                            participant_repo.create(connection, new_participant)
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
                existing = participant_repo.get(connection, event.args["id"])
            open_form(existing)

        def on_remove(event) -> None:
            """Table row-delete handler: delete the participant after confirmation.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            participant_id = event.args["id"]
            name = event.args["name"]

            with ui.dialog() as confirm, ui.card():
                ui.label(f'"{name}" wirklich löschen?')
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            participant_repo.delete(connection, participant_id)
                        confirm.close()
                        refresh()
                        ui.notify("Gelöscht.", type="warning")

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("edit", on_edit)
        table.on("remove", on_remove)

        ui.button("+ Neuer Teilnehmer", on_click=lambda: open_form(None)).classes("mt-2")

        refresh()
