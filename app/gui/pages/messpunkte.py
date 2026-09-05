"""Messpunkte management page: list, search, create, edit, delete, and a
detail drill-down showing the Standort, LEG and currently assigned Person.
"""

from datetime import date

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.messpunkt_form import open_messpunkt_form
from app.gui.navigation import page_frame
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
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


#: `(label, field)` pairs for the printed table -- `field` matches the
#: keys `_to_row` puts into each row dict.
PRINT_COLUMNS = [
    ("Messpunkt", "messpunkt_bezeichnung"),
    ("Messrichtung", "messrichtung"),
    ("Standort-Adresse", "standort_adresse"),
    ("LEG", "leg"),
    ("Zugeordnet", "person"),
    ("PV-Leistung (kWp)", "pv_leistung_kwp"),
    ("Batteriespeicher (kWh)", "batteriespeicher_kwh"),
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


def _to_row(connection, mp: Messpunkt, standorte: dict, legs: dict) -> dict:
    """Convert a `Messpunkt` into a row dict for the card-based list.

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
        "pv_leistung_kwp": mp.pv_leistung_kwp,
        "batteriespeicher_kwh": mp.batteriespeicher_kwh,
        "_search": search_text,
    }


@ui.page("/messpunkte")
def messpunkte_page() -> None:
    """Render the Messpunkte CRUD page with search.

    Returns:
        None.
    """
    with page_frame("/messpunkte", "Messpunkte"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Messpunkte sind fix an einen Standort gebunden. Die LEG wird "
                "hier pro Messpunkt zugewiesen -- zwei Messpunkte am selben "
                "Standort können unterschiedlichen LEGs angehören. Wer über "
                "einen Messpunkt abgerechnet wird, legen Sie unter "
                "„Zuordnungen“ fest."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    rubrik="Messpunkte",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: visible_rows,
                    get_filter_description=lambda: (
                        f'Suche: "{search_input.value.strip()}"' if search_input.value else None
                    ),
                )
                ui.button("+ Neuer Messpunkt", on_click=lambda: open_form(None))

        search_input = ui.input("Suche (Bezeichnung, Richtung, Standort, LEG, Person...)").classes(
            "w-full max-w-md"
        ).props("debounce=300 clearable")

        list_container = ui.column().classes("w-full gap-2 mt-2")

        all_rows: list[dict] = []
        visible_rows: list[dict] = []

        def render_card(row: dict) -> None:
            """Render one Messpunkt as a card with wrapping field groups.

            Args:
                row: Row dict built by `_to_row`.

            Returns:
                None.
            """
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-start gap-6 flex-wrap"):
                    with ui.column().classes("gap-0 min-w-[220px]"):
                        ui.label(row["messpunkt_bezeichnung"]).classes("font-bold")
                        ui.label(row["messrichtung"]).classes("text-caption text-grey-6")
                    with ui.column().classes("gap-0 min-w-[220px]"):
                        ui.label(row["standort_adresse"])
                        ui.label(f"LEG: {row['leg']}").classes("text-grey-7")
                    with ui.column().classes("gap-0 min-w-[180px]"):
                        ui.label(f"Zugeordnet: {row['person']}")
                        extras = []
                        if row["pv_leistung_kwp"] is not None:
                            extras.append(f"PV {row['pv_leistung_kwp']:g} kWp")
                        if row["batteriespeicher_kwh"] is not None:
                            extras.append(f"Speicher {row['batteriespeicher_kwh']:g} kWh")
                        if extras:
                            ui.label(", ".join(extras)).classes("text-grey-7 text-caption")
                    with ui.row().classes("gap-1 ml-auto"):
                        ui.button(
                            icon="visibility",
                            on_click=lambda r=row: ui.navigate.to(f"/messpunkte/{r['id']}"),
                        ).props("dense flat")
                        ui.button(icon="edit", on_click=lambda r=row: on_edit(r)).props("dense flat")
                        ui.button(icon="delete", on_click=lambda r=row: on_remove(r)).props(
                            "dense flat color=negative"
                        )

        def apply_filter() -> None:
            """Filter the currently loaded rows by the search input's value.

            Returns:
                None.
            """
            nonlocal visible_rows
            needle = (search_input.value or "").strip().lower()
            visible_rows = [r for r in all_rows if needle in r["_search"]] if needle else list(all_rows)
            list_container.clear()
            with list_container:
                if not visible_rows:
                    ui.label("Keine Messpunkte gefunden.")
                for row in visible_rows:
                    render_card(row)

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
            open_messpunkt_form(existing=existing, on_saved=lambda _: refresh())

        def on_edit(row: dict) -> None:
            """Card edit-button handler: open the edit dialog for this Messpunkt.

            Args:
                row: Row dict built by `_to_row`.

            Returns:
                None.
            """
            with connection_scope() as connection:
                existing = messpunkt_repo.get(connection, row["id"])
            open_form(existing)

        def on_remove(row: dict) -> None:
            """Card delete-button handler: delete the Messpunkt after confirmation.

            Args:
                row: Row dict built by `_to_row`.

            Returns:
                None.
            """
            messpunkt_id = row["id"]
            bezeichnung_text = row["messpunkt_bezeichnung"]

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
                        # notify before refresh() -- see save() above for why
                        safe_notify("Gelöscht.", type="warning")
                        refresh()

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

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
            if mp.pv_leistung_kwp is not None:
                ui.label(f"PV-Leistung: {mp.pv_leistung_kwp:g} kWp")
            if mp.batteriespeicher_kwh is not None:
                ui.label(f"Batteriespeicher: {mp.batteriespeicher_kwh:g} kWh")
