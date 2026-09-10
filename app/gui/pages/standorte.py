"""Standorte management page: list, search, create, edit, delete, and a
detail drill-down showing the site's Messpunkte.
"""

from datetime import date, datetime

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.gui.print_list import render_print_button, table_columns
from app.gui.safe_notify import safe_notify
from app.gui.standort_form import open_standort_form
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import standort as standort_repo
from app.models import substation_area as substation_area_repo
from app.models import zuordnung as zuordnung_repo
from app.models.standort import Standort

COLUMNS = [
    {"name": "adresse", "label": "Adresse", "field": "adresse", "align": "left", "sortable": True},
    {"name": "plz_gemeinde", "label": "PLZ / Gemeinde", "field": "plz_gemeinde", "align": "left"},
    {"name": "lage", "label": "Lage", "field": "lage", "align": "left"},
    {"name": "substation_area", "label": "Trafokreis", "field": "substation_area", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]


def _current_person_display(connection, messpunkt_id: int) -> tuple[str, bool]:
    """Find the name of the Person (currently or soon) assigned to a Messpunkt.

    Args:
        connection: Open SQLite connection.
        messpunkt_id: Primary key of the metering point.

    Returns:
        `(name, is_future)`, see `app.gui.pages.messpunkte._current_person_display`
        (identical logic, duplicated here since this page needs its own
        `ui.table`-row shape) -- `name` is "-" if there is no current or
        upcoming Zuordnung at all.
    """
    zuordnung = zuordnung_repo.get_relevant_for_messpunkt(connection, messpunkt_id, datetime.now())
    if zuordnung is None:
        return "-", False
    person = person_repo.get(connection, zuordnung.person_id)
    name = person.anzeige_name if person else "?"
    is_future = zuordnung.gueltig_von > date.today()
    return name, is_future


def _to_row(standort: Standort, substation_areas: dict) -> dict:
    """Convert a `Standort` into a row dict for the NiceGUI table.

    Args:
        standort: Standort to convert.
        substation areas: Preloaded `{substation_area_id: substation area}` lookup.

    Returns:
        A dict with the fields required by `COLUMNS`, plus a hidden
        `_search` key used for client-side filtering.
    """
    substation_area = substation_areas.get(standort.substation_area_id)
    substation_area_name = substation_area.name if substation_area else "-"
    search_text = " ".join(
        [
            standort.adresse,
            standort.hausnummer,
            standort.plz,
            standort.gemeinde,
            standort.lage or "",
            substation_area_name,
        ]
    ).lower()
    return {
        "id": standort.id,
        "adresse": f"{standort.adresse} {standort.hausnummer}".strip(),
        "plz_gemeinde": f"{standort.plz} {standort.gemeinde}".strip(),
        "lage": standort.lage,
        "substation_area": substation_area_name,
        "_search": search_text,
    }


@ui.page("/standorte")
def standorte_page() -> None:
    """Render the Standorte CRUD page with search.

    Returns:
        None.
    """
    with page_frame("/standorte", "Standorte"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Standorte sind physische Netzanschlusspunkte. Ein Standort "
                "gehört zu genau einem Trafokreis; die Zuordnung erfolgt "
                "manuell -- im Trafokreis-Feld die (Teil-)Bezeichnung "
                "eintippen, um passende Trafokreise zu finden. Die LEG wird "
                "nicht hier, sondern pro Messpunkt zugewiesen (siehe "
                "„Messpunkte“)."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    rubrik="Standorte",
                    get_columns=lambda: table_columns(table),
                    get_rows=lambda: table.rows,
                    get_filter_description=lambda: (
                        f'Suche: "{search_input.value.strip()}"' if search_input.value else None
                    ),
                )
                ui.button("+ Neuer Standort", on_click=lambda: open_form(None))

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
                substation_areas = {t.id: t for t in substation_area_repo.list_all(connection)}
                all_rows = [_to_row(s, substation_areas) for s in standort_repo.list_all(connection)]
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())

        def open_form(existing: Standort | None) -> None:
            """Open the create/edit dialog for a Standort.

            Args:
                existing: Standort to edit, or `None` to create a new one.

            Returns:
                None.
            """
            open_standort_form(existing=existing, on_saved=lambda _: refresh())

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
                        # notify before refresh() -- see app.gui.safe_notify's
                        # module docstring for why a plain ui.notify() here
                        # can raise "parent element ... has been deleted"
                        # once the dialog it was called from is gone.
                        safe_notify("Gelöscht.", type="warning")
                        refresh()

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("view", on_view)
        table.on("edit", on_edit)
        table.on("remove", on_remove)

        refresh()


@ui.page("/standorte/{standort_id}")
def standort_detail_page(standort_id: int) -> None:
    """Render one Standort's detail view: Adresse, Lage, substation area, and its
    Messpunkte (each with its own LEG, see `app.models.leg`).

    Args:
        standort_id: Database id of the Standort, from the URL path.

    Returns:
        None.
    """
    with connection_scope() as connection:
        standort = standort_repo.get(connection, standort_id)
        substation_area = (
            substation_area_repo.get(connection, standort.substation_area_id)
            if standort and standort.substation_area_id
            else None
        )
        messpunkte = messpunkt_repo.list_for_standort(connection, standort_id) if standort else []
        legs = {leg.id: leg for leg in leg_repo.list_all(connection)}
        person_display = {mp.id: _current_person_display(connection, mp.id) for mp in messpunkte}

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
            ui.label(f"Trafokreis: {substation_area.name if substation_area else '-'}")

        ui.label("Messpunkte an diesem Standort").classes("text-lg font-bold mt-6")
        if messpunkte:
            messpunkte_table = ui.table(
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
                        "person": person_display.get(mp.id, ("-", False))[0],
                        "person_is_future": person_display.get(mp.id, ("-", False))[1],
                    }
                    for mp in messpunkte
                ],
                row_key="id",
            ).classes("w-full mt-2")
            messpunkte_table.add_slot(
                "body-cell-person",
                r'''
                <q-td :props="props" :class="props.row.person_is_future ? 'text-orange-8' : ''">
                    {{ props.value }}<span v-if="props.row.person_is_future"> (bevorstehend)</span>
                </q-td>
                ''',
            )
        else:
            ui.label("Keine Messpunkte an diesem Standort.")
