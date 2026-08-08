"""Standorte management page: list, search, create, edit, delete, and a
detail drill-down showing the site's Messpunkte.
"""

from datetime import date

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import standort as standort_repo
from app.models import trafokreis as trafokreis_repo
from app.models import zuordnung as zuordnung_repo
from app.models.standort import Standort

COLUMNS = [
    {"name": "adresse", "label": "Adresse", "field": "adresse", "align": "left", "sortable": True},
    {"name": "plz_gemeinde", "label": "PLZ / Gemeinde", "field": "plz_gemeinde", "align": "left"},
    {"name": "lage", "label": "Lage", "field": "lage", "align": "left"},
    {"name": "trafokreis", "label": "Trafokreis", "field": "trafokreis", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]


def _current_person_name(connection, messpunkt_id: int) -> str:
    """Find the name of the Person currently assigned to a Messpunkt.

    Args:
        connection: Open SQLite connection.
        messpunkt_id: Primary key of the metering point.

    Returns:
        The current person's name, or "-" if unassigned today.
    """
    today = date.today()
    for z in zuordnung_repo.list_for_messpunkt(connection, messpunkt_id):
        if z.gueltig_von <= today and (z.gueltig_bis is None or z.gueltig_bis >= today):
            person = person_repo.get(connection, z.person_id)
            return person.anzeige_name if person else "?"
    return "-"


def _to_row(standort: Standort, trafokreise: dict) -> dict:
    """Convert a `Standort` into a row dict for the NiceGUI table.

    Args:
        standort: Standort to convert.
        trafokreise: Preloaded `{trafokreis_id: Trafokreis}` lookup.

    Returns:
        A dict with the fields required by `COLUMNS`, plus a hidden
        `_search` key used for client-side filtering.
    """
    trafokreis = trafokreise.get(standort.trafokreis_id)
    trafokreis_name = trafokreis.name if trafokreis else "-"
    search_text = " ".join(
        [
            standort.adresse,
            standort.hausnummer,
            standort.plz,
            standort.gemeinde,
            standort.lage or "",
            trafokreis_name,
        ]
    ).lower()
    return {
        "id": standort.id,
        "adresse": f"{standort.adresse} {standort.hausnummer}".strip(),
        "plz_gemeinde": f"{standort.plz} {standort.gemeinde}".strip(),
        "lage": standort.lage,
        "trafokreis": trafokreis_name,
        "_search": search_text,
    }


@ui.page("/standorte")
def standorte_page() -> None:
    """Render the Standorte CRUD page with search.

    Returns:
        None.
    """
    with page_frame("/standorte", "Standorte"):
        ui.label(
            "Standorte sind physische Netzanschlusspunkte. Ein Standort "
            "gehört zu genau einem Trafokreis; die Zuordnung erfolgt "
            "manuell -- im Trafokreis-Feld die (Teil-)Bezeichnung "
            "eintippen, um passende Trafokreise zu finden. Die LEG wird "
            "nicht hier, sondern pro Messpunkt zugewiesen (siehe "
            "„Messpunkte“)."
        ).classes("text-body2 text-grey-8")

        search_input = ui.input("Suche (Adresse, PLZ, Gemeinde, Trafokreis...)").classes(
            "w-full max-w-md"
        ).props("debounce=300 clearable")

        table = ui.table(columns=COLUMNS, rows=[], row_key="id").classes("w-full")
        table.add_slot(
            "body-cell-actions",
            r'''
            <q-td :props="props">
                <q-btn dense flat icon="visibility" @click="() => $parent.$emit('view', props.row)" />
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
            """Reload all Standorte from the database and re-apply the filter.

            Returns:
                None.
            """
            nonlocal all_rows
            with connection_scope() as connection:
                trafokreise = {t.id: t for t in trafokreis_repo.list_all(connection)}
                all_rows = [_to_row(s, trafokreise) for s in standort_repo.list_all(connection)]
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())

        def open_form(existing: Standort | None) -> None:
            """Open the create/edit dialog for a Standort.

            Args:
                existing: Standort to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with connection_scope() as connection:
                trafokreise = trafokreis_repo.list_all(connection)
            trafokreis_options = {t.id: t.name for t in trafokreise}

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Standort bearbeiten" if existing else "Neuer Standort").classes(
                    "text-lg font-bold"
                )
                with ui.row().classes("w-full gap-2"):
                    adresse = ui.input(
                        "Adresse", value=existing.adresse if existing else ""
                    ).classes("flex-grow").props("debounce=300")
                    hausnummer = ui.input(
                        "Hausnummer", value=existing.hausnummer if existing else ""
                    ).classes("w-24").props("debounce=300")
                with ui.row().classes("w-full gap-2"):
                    plz = ui.input(
                        "PLZ", value=existing.plz if existing else ""
                    ).classes("w-24").props("debounce=300")
                    gemeinde = ui.input(
                        "Gemeinde", value=existing.gemeinde if existing else ""
                    ).classes("flex-grow")
                duplicate_warning = ui.label("").classes("text-warning")
                lage = ui.input(
                    "Lage (optional, z. B. Stockwerk)", value=existing.lage if existing else ""
                ).classes("w-full")
                trafokreis_select = ui.select(
                    trafokreis_options,
                    label="Trafokreis",
                    value=existing.trafokreis_id if existing else None,
                    with_input=True,
                ).classes("w-full")
                error_label = ui.label("").classes("text-negative")

                def check_duplicate() -> bool:
                    """Check whether Adresse/Hausnummer/PLZ already match another Standort.

                    Updates `duplicate_warning` as a side effect.

                    Returns:
                        `True` if a different Standort already has this
                        exact address.
                    """
                    if not (adresse.value.strip() and hausnummer.value.strip() and plz.value.strip()):
                        duplicate_warning.text = ""
                        return False
                    with connection_scope() as connection:
                        found = standort_repo.find_by_address(
                            connection, adresse.value.strip(), hausnummer.value.strip(), plz.value.strip()
                        )
                    is_duplicate = found is not None and (existing is None or found.id != existing.id)
                    duplicate_warning.text = (
                        "Dieser Standort (Adresse, Hausnummer, PLZ) existiert bereits."
                        if is_duplicate
                        else ""
                    )
                    return is_duplicate

                adresse.on_value_change(lambda _: check_duplicate())
                hausnummer.on_value_change(lambda _: check_duplicate())
                plz.on_value_change(lambda _: check_duplicate())

                def save() -> None:
                    """Validate the form and persist the Standort.

                    Returns:
                        None.
                    """
                    if not adresse.value.strip():
                        error_label.text = "Adresse darf nicht leer sein."
                        return
                    if check_duplicate():
                        error_label.text = "Dieser Standort (Adresse, Hausnummer, PLZ) existiert bereits."
                        return
                    with connection_scope() as connection:
                        if existing:
                            updated = Standort(
                                id=existing.id,
                                adresse=adresse.value.strip(),
                                hausnummer=hausnummer.value.strip(),
                                plz=plz.value.strip(),
                                gemeinde=gemeinde.value.strip(),
                                lage=lage.value.strip(),
                                trafokreis_id=trafokreis_select.value,
                                created_at=existing.created_at,
                            )
                            standort_repo.update(connection, updated)
                        else:
                            new_standort = Standort(
                                id=None,
                                adresse=adresse.value.strip(),
                                hausnummer=hausnummer.value.strip(),
                                plz=plz.value.strip(),
                                gemeinde=gemeinde.value.strip(),
                                lage=lage.value.strip(),
                                trafokreis_id=trafokreis_select.value,
                                created_at="",
                            )
                            standort_repo.create(connection, new_standort)
                    dialog.close()
                    refresh()
                    ui.notify("Gespeichert.", type="positive")

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    ui.button("Speichern", on_click=save)
            dialog.open()

        def on_view(event) -> None:
            """Table row-view handler: navigate to the Standort's detail page.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            ui.navigate.to(f"/standorte/{event.args['id']}")

        def on_edit(event) -> None:
            """Table row-edit handler: open the edit dialog for the clicked row.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            with connection_scope() as connection:
                existing = standort_repo.get(connection, event.args["id"])
            open_form(existing)

        def on_remove(event) -> None:
            """Table row-delete handler: delete the Standort after confirmation.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            standort_id = event.args["id"]
            adresse_text = event.args["adresse"]

            with ui.dialog() as confirm, ui.card():
                ui.label(
                    f'Standort "{adresse_text}" wirklich löschen? '
                    "Zugehörige Messpunkte werden mitgelöscht."
                )
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            standort_repo.delete(connection, standort_id)
                        confirm.close()
                        refresh()
                        ui.notify("Gelöscht.", type="warning")

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("view", on_view)
        table.on("edit", on_edit)
        table.on("remove", on_remove)

        ui.button("+ Neuer Standort", on_click=lambda: open_form(None)).classes("mt-2")

        refresh()


@ui.page("/standorte/{standort_id}")
def standort_detail_page(standort_id: int) -> None:
    """Render one Standort's detail view: Adresse, Lage, Trafokreis, and its
    Messpunkte (each with its own LEG, see `app.models.leg`).

    Args:
        standort_id: Database id of the Standort, from the URL path.

    Returns:
        None.
    """
    with connection_scope() as connection:
        standort = standort_repo.get(connection, standort_id)
        trafokreis = (
            trafokreis_repo.get(connection, standort.trafokreis_id)
            if standort and standort.trafokreis_id
            else None
        )
        messpunkte = messpunkt_repo.list_for_standort(connection, standort_id) if standort else []
        legs = {leg.id: leg for leg in leg_repo.list_all(connection)}
        person_names = {mp.id: _current_person_name(connection, mp.id) for mp in messpunkte}

    with page_frame(
        "/standorte", "Standort" if standort is None else standort.adresse_vollstaendig
    ):
        if standort is None:
            ui.label("Standort nicht gefunden.").classes("text-negative")
            ui.link("← Zurück zu Standorten", "/standorte")
            return

        ui.link("← Zurück zu Standorten", "/standorte")
        ui.label(standort.adresse_vollstaendig).classes("text-xl font-bold mt-2")
        with ui.card().classes("w-full max-w-lg"):
            ui.label(f"Lage: {standort.lage or '-'}")
            ui.label(f"Trafokreis: {trafokreis.name if trafokreis else '-'}")

        ui.label("Messpunkte an diesem Standort").classes("text-lg font-bold mt-6")
        if messpunkte:
            ui.table(
                columns=[
                    {"name": "messpunkt_bezeichnung", "label": "Bezeichnung", "field": "messpunkt_bezeichnung", "align": "left"},
                    {"name": "messrichtung", "label": "Messrichtung", "field": "messrichtung", "align": "left"},
                    {"name": "leg", "label": "LEG", "field": "leg", "align": "left"},
                    {"name": "person", "label": "Aktuell zugeordnet", "field": "person", "align": "left"},
                ],
                rows=[
                    {
                        "id": mp.id,
                        "messpunkt_bezeichnung": mp.messpunkt_bezeichnung,
                        "messrichtung": "Bezug" if mp.is_bezug else "Einspeisung",
                        "leg": legs[mp.leg_id].name if mp.leg_id in legs else "-",
                        "person": person_names.get(mp.id, "-"),
                    }
                    for mp in messpunkte
                ],
                row_key="id",
            ).classes("w-full mt-2")
        else:
            ui.label("Keine Messpunkte an diesem Standort.")
