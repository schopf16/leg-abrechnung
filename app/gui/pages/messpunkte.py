"""Messpunkte management page: list, search, create, edit, delete, and a
detail drill-down showing the Standort, LEG and currently assigned Person.
"""

from datetime import date

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import standort as standort_repo
from app.models import zuordnung as zuordnung_repo
from app.models.messpunkt import (
    MESSRICHTUNG_BEZUG,
    MESSRICHTUNG_EINSPEISUNG,
    Messpunkt,
)

MESSRICHTUNG_LABELS = {
    MESSRICHTUNG_BEZUG: "Bezug",
    MESSRICHTUNG_EINSPEISUNG: "Einspeisung",
}

COLUMNS = [
    {"name": "messpunkt_bezeichnung", "label": "Messpunkt-Bezeichnung", "field": "messpunkt_bezeichnung", "align": "left", "sortable": True},
    {"name": "messrichtung", "label": "Messrichtung", "field": "messrichtung", "align": "left"},
    {"name": "standort_adresse", "label": "Standort", "field": "standort_adresse", "align": "left"},
    {"name": "leg", "label": "LEG", "field": "leg", "align": "left"},
    {"name": "person", "label": "Aktuell zugeordnet", "field": "person", "align": "left"},
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
            return person.name if person else "?"
    return "-"


def _to_row(connection, mp: Messpunkt, standorte: dict, legs: dict) -> dict:
    """Convert a `Messpunkt` into a row dict for the NiceGUI table.

    Args:
        connection: Open SQLite connection.
        mp: Messpunkt to convert.
        standorte: Preloaded `{standort_id: Standort}` lookup.
        legs: Preloaded `{leg_id: Leg}` lookup.

    Returns:
        A dict with the fields required by `COLUMNS`, plus a hidden
        `_search` key used for client-side filtering.
    """
    standort = standorte.get(mp.standort_id)
    standort_adresse = standort.adresse_vollstaendig if standort else "?"
    leg = legs.get(mp.leg_id)
    leg_name = leg.name if leg else "-"
    person_name = _current_person_name(connection, mp.id)
    search_text = " ".join(
        [
            mp.messpunkt_bezeichnung,
            MESSRICHTUNG_LABELS.get(mp.messrichtung, mp.messrichtung),
            standort_adresse,
            leg_name,
            person_name,
        ]
    ).lower()
    return {
        "id": mp.id,
        "messpunkt_bezeichnung": mp.messpunkt_bezeichnung,
        "messrichtung": MESSRICHTUNG_LABELS.get(mp.messrichtung, mp.messrichtung),
        "standort_id": mp.standort_id,
        "standort_adresse": standort_adresse,
        "leg": leg_name,
        "person": person_name,
        "_search": search_text,
    }


@ui.page("/messpunkte")
def messpunkte_page() -> None:
    """Render the Messpunkte CRUD page with search.

    Returns:
        None.
    """
    with page_frame("/messpunkte", "Messpunkte"):
        ui.label(
            "Messpunkte sind fix an einen Standort gebunden. Die LEG wird "
            "hier pro Messpunkt zugewiesen -- zwei Messpunkte am selben "
            "Standort können unterschiedlichen LEGs angehören. Wer über "
            "einen Messpunkt abgerechnet wird, legen Sie unter "
            "„Zuordnungen“ fest."
        ).classes("text-body2 text-grey-8")

        search_input = ui.input("Suche (Bezeichnung, Richtung, Standort, LEG, Person...)").classes(
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
            """Reload all Messpunkte from the database and re-apply the filter.

            Returns:
                None.
            """
            nonlocal all_rows
            with connection_scope() as connection:
                standorte = {s.id: s for s in standort_repo.list_all(connection)}
                legs = {leg.id: leg for leg in leg_repo.list_all(connection)}
                all_rows = [
                    _to_row(connection, mp, standorte, legs) for mp in messpunkt_repo.list_all(connection)
                ]
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())

        def open_form(existing: Messpunkt | None) -> None:
            """Open the create/edit dialog for a Messpunkt.

            Args:
                existing: Messpunkt to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with connection_scope() as connection:
                standorte = standort_repo.list_all(connection)
                legs = leg_repo.list_all(connection)
            standort_options = {s.id: s.adresse_vollstaendig for s in standorte}
            leg_options = {leg.id: leg.name for leg in legs}

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Messpunkt bearbeiten" if existing else "Neuer Messpunkt").classes(
                    "text-lg font-bold"
                )
                bezeichnung = ui.input(
                    "Messpunkt-Bezeichnung (aus BKW-Daten)",
                    value=existing.messpunkt_bezeichnung if existing else "",
                ).classes("w-full")
                messrichtung = ui.select(
                    MESSRICHTUNG_LABELS,
                    label="Messrichtung",
                    value=existing.messrichtung if existing else MESSRICHTUNG_BEZUG,
                ).classes("w-full")
                standort_select = ui.select(
                    standort_options,
                    label="Standort",
                    value=existing.standort_id if existing else (standorte[0].id if standorte else None),
                ).classes("w-full")
                leg_select = ui.select(
                    leg_options,
                    label="LEG",
                    value=existing.leg_id if existing else None,
                    with_input=True,
                ).classes("w-full")
                error_label = ui.label("").classes("text-negative")

                def save() -> None:
                    """Validate the form and persist the Messpunkt.

                    Returns:
                        None.
                    """
                    if not bezeichnung.value.strip():
                        error_label.text = "Messpunkt-Bezeichnung darf nicht leer sein."
                        return
                    if standort_select.value is None:
                        error_label.text = "Standort ist erforderlich."
                        return
                    try:
                        with connection_scope() as connection:
                            if existing:
                                updated = Messpunkt(
                                    id=existing.id,
                                    messpunkt_bezeichnung=bezeichnung.value.strip(),
                                    messrichtung=messrichtung.value,
                                    standort_id=standort_select.value,
                                    leg_id=leg_select.value,
                                    created_at=existing.created_at,
                                )
                                messpunkt_repo.update(connection, updated)
                            else:
                                new_mp = Messpunkt(
                                    id=None,
                                    messpunkt_bezeichnung=bezeichnung.value.strip(),
                                    messrichtung=messrichtung.value,
                                    standort_id=standort_select.value,
                                    leg_id=leg_select.value,
                                    created_at="",
                                )
                                messpunkt_repo.create(connection, new_mp)
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

        def on_view(event) -> None:
            """Table row-view handler: navigate to the Messpunkt's detail page.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            ui.navigate.to(f"/messpunkte/{event.args['id']}")

        def on_edit(event) -> None:
            """Table row-edit handler: open the edit dialog for the clicked row.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            with connection_scope() as connection:
                existing = messpunkt_repo.get(connection, event.args["id"])
            open_form(existing)

        def on_remove(event) -> None:
            """Table row-delete handler: delete the Messpunkt after confirmation.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            messpunkt_id = event.args["id"]
            bezeichnung_text = event.args["messpunkt_bezeichnung"]

            with ui.dialog() as confirm, ui.card():
                ui.label(
                    f'Messpunkt "{bezeichnung_text}" wirklich löschen? '
                    "Zugehörige Zuordnungen und Messwerte werden mitgelöscht."
                )
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            messpunkt_repo.delete(connection, messpunkt_id)
                        confirm.close()
                        refresh()
                        ui.notify("Gelöscht.", type="warning")

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("view", on_view)
        table.on("edit", on_edit)
        table.on("remove", on_remove)

        ui.button("+ Neuer Messpunkt", on_click=lambda: open_form(None)).classes("mt-2")

        refresh()


@ui.page("/messpunkte/{messpunkt_id}")
def messpunkt_detail_page(messpunkt_id: int) -> None:
    """Render one Messpunkt's detail view: Bezeichnung, Messrichtung,
    Standort, LEG and currently assigned Person.

    Args:
        messpunkt_id: Database id of the Messpunkt, from the URL path.

    Returns:
        None.
    """
    with connection_scope() as connection:
        mp = messpunkt_repo.get(connection, messpunkt_id)
        standort = standort_repo.get(connection, mp.standort_id) if mp else None
        leg = leg_repo.get(connection, mp.leg_id) if mp and mp.leg_id else None
        person_name = _current_person_name(connection, messpunkt_id) if mp else "-"

    with page_frame(
        "/messpunkte", "Messpunkt" if mp is None else mp.messpunkt_bezeichnung
    ):
        if mp is None:
            ui.label("Messpunkt nicht gefunden.").classes("text-negative")
            ui.link("← Zurück zu Messpunkten", "/messpunkte")
            return

        ui.link("← Zurück zu Messpunkten", "/messpunkte")
        ui.label(mp.messpunkt_bezeichnung).classes("text-xl font-bold mt-2")
        with ui.card().classes("w-full max-w-lg"):
            ui.label(f"Messrichtung: {MESSRICHTUNG_LABELS.get(mp.messrichtung, mp.messrichtung)}")
            ui.label(f"Standort: {standort.adresse_vollstaendig if standort else '?'}")
            if standort:
                ui.link("Standort ansehen", f"/standorte/{standort.id}")
            ui.label(f"LEG: {leg.name if leg else '-'}")
            ui.label(f"Aktuell zugeordnete Person: {person_name}")
