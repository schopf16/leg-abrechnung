"""metering points management page: list, search, create, edit, delete, and a
detail drill-down showing the site, LEG and currently assigned Person.
"""

from datetime import date, datetime

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.metering_point_form import open_metering_point_form
from app.domain.quality_checks import SUBJECT_METERING_POINT
from app.gui.filter_bar import FilterBar
from app.gui.detail_header import render_detail_header
from app.gui.navigation import page_frame
from app.gui.problem_markers import (
    TABLE_MARKER_HTML,
    load_problems,
    render_problem_notes,
)
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.gui.table_list import paged_table
from app.gui.sorting import (
    SortOption,
    address_key,
    apply_sort,
    sort_description,
    text_key,
)
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import assignment as assignment_repo
from app.models.metering_point import (
    DIRECTION_CONSUMPTION,
    DIRECTION_FEED_IN,
    MeteringPoint,
)

DIRECTION_LABELS = {
    DIRECTION_CONSUMPTION: "Bezug",
    DIRECTION_FEED_IN: "Einspeisung",
}


#: `(label, field)` pairs for the printed table -- `field` matches the
#: keys `_to_row` puts into each row dict.
PRINT_COLUMNS = [
    ("Messpunkt", "designation"),
    ("Bezeichnung", "label"),
    ("Messrichtung", "direction"),
    ("Standort-Adresse", "site_address"),
    ("LEG", "leg"),
    ("Zugeordnet", "person"),
    ("PV-Leistung (kWp)", "pv_capacity_kwp"),
    ("Batteriespeicher (kWh)", "battery_capacity_kwh"),
]


#: What the list shows. PV and battery capacity are on the detail page: they
#: are filled in for a handful of metering points and would be two almost
#: always empty columns here.
COLUMNS = [
    {"name": "designation", "label": "Messpunkt", "field": "designation", "align": "left"},
    {"name": "label", "label": "Bezeichnung", "field": "label", "align": "left"},
    {"name": "direction", "label": "Messrichtung", "field": "direction", "align": "left"},
    {"name": "site", "label": "Standort", "field": "site", "align": "left"},
    {"name": "leg", "label": "LEG", "field": "leg", "align": "left"},
    {"name": "person", "label": "Zugeordnet", "field": "person", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]

#: Orders the Messpunkte list offers, default first.
SORT_OPTIONS = [
    SortOption("designation", "Messpunkt", lambda row: text_key(row["designation"])),
    SortOption(
        "address",
        "Adresse",
        lambda row: (address_key(row["site_street"], "", row["site_city"]), text_key(row["designation"])),
    ),
    SortOption("leg", "LEG", lambda row: text_key(row["leg"], row["designation"])),
    SortOption("person", "Zugeordnete Person", lambda row: text_key(row["person"], row["designation"])),
    SortOption("direction", "Messrichtung", lambda row: text_key(row["direction"], row["designation"])),
    SortOption("label", "Bezeichnung", lambda row: text_key(row["label"], row["designation"])),
]


def _copy_metering_point_designation(designation: str) -> None:
    """Copy a MeteringPoint's designation to the clipboard and confirm.

    Args:
        designation: The metering point designation to copy.

    Returns:
        None.
    """
    ui.clipboard.write(designation)
    safe_notify("Messpunktbezeichnung kopiert.")


def _metering_point_designation_row(designation: str, *, classes: str = "font-bold") -> None:
    """Render the metering point designation with an inline copy-to-clipboard
    button (same pattern as `app.gui.pages.persons._customer_number_row`).

    Args:
        designation: The metering point designation to show.
        classes: CSS classes applied to the label itself.

    Returns:
        None.
    """
    with ui.row().classes("items-center gap-1"):
        ui.label(designation).classes(classes)
        ui.button(
            icon="content_copy",
            on_click=lambda: _copy_metering_point_designation(designation),
        ).props("dense flat size=sm").tooltip("Messpunktbezeichnung kopieren")


def _current_person_display(connection, metering_point_id: int) -> tuple[str, bool]:
    """Find the name of the Person (currently or soon) assigned to a MeteringPoint.

    Args:
        connection: Open SQLite connection.
        metering_point_id: Primary key of the metering point.

    Returns:
        `(name, is_future)` -- `name` is "-" if there is no current or
        upcoming Assignment at all (see `app.models.assignment.
        get_relevant_for_metering_point`); `is_future` is `True` if the
        assignment shown has not started yet, so the caller can mark it
        visually without spelling out the exact date.
    """
    assignment = assignment_repo.get_relevant_for_metering_point(
        connection, metering_point_id, datetime.now()
    )
    if assignment is None:
        return "-", False
    person = person_repo.get(connection, assignment.person_id)
    name = person.display_name if person else "?"
    is_future = assignment.valid_from > date.today()
    return name, is_future


def _to_row(connection, mp: MeteringPoint, sites: dict, legs: dict) -> dict:
    """Convert a `MeteringPoint` into a row dict for the card-based list.

    Args:
        connection: Open SQLite connection.
        mp: MeteringPoint to convert.
        sites: Preloaded `{site_id: site}` lookup.
        legs: Preloaded `{leg_id: Leg}` lookup.

    Returns:
        A dict with the fields required by `COLUMNS`, plus a hidden
        `_search` key used for client-side filtering.
    """
    site = sites.get(mp.site_id)
    site_address = site.full_address if site else "?"
    site_street = " ".join(p for p in (site.street, site.house_number) if p) if site else "?"
    site_city = " ".join(p for p in (site.postal_code, site.municipality) if p) if site else ""
    leg = legs.get(mp.leg_id)
    leg_name = leg.name if leg else "-"
    person_name, person_is_future = _current_person_display(connection, mp.id)
    search_text = " ".join(
        [
            mp.designation,
            mp.label,
            DIRECTION_LABELS.get(mp.direction, mp.direction),
            site_address,
            leg_name,
            person_name,
        ]
    ).lower()
    return {
        "id": mp.id,
        "designation": mp.designation,
        "label": mp.label,
        "direction": DIRECTION_LABELS.get(mp.direction, mp.direction),
        "site_id": mp.site_id,
        "site_address": site_address,
        "site_street": site_street,
        "site_city": site_city,
        # One cell for the table; the card had two lines for it.
        "site": ", ".join(part for part in (site_street, site_city) if part),
        "leg": leg_name,
        "person": person_name,
        "person_is_future": person_is_future,
        "pv_capacity_kwp": mp.pv_capacity_kwp,
        "battery_capacity_kwh": mp.battery_capacity_kwh,
        "_search": search_text,
    }


@ui.page("/metering-points")
def metering_points_page() -> None:
    """Render the metering points CRUD page with search.

    Returns:
        None.
    """
    with page_frame("/metering-points", "Messpunkte"):
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
                    heading="Messpunkte",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: visible_rows,
                    get_filter_description=lambda: (
                        " / ".join(
                            filter(
                                None,
                                [
                                    f'Suche: "{search_input.value.strip()}"' if search_input.value else None,
                                    "Nur ohne Zuordnung" if without_assignment_switch.value else None,
                                ],
                            )
                        )
                        or None
                    ),
                    # Named on its own line: a printout is read away from
                    # the screen, where the order is not self-evident.
                    get_sort_description=lambda: sort_description(SORT_OPTIONS, sort_select),
                )
                ui.button("+ Neuer Messpunkt", on_click=lambda: open_form(None))

        bar = FilterBar("/metering-points")
        search_input = bar.search("Bezeichnung, Richtung, Standort, LEG, Person")
        sort_select = bar.sort(SORT_OPTIONS, lambda: apply_filter())
        without_assignment_switch = bar.filter("Nur ohne Zuordnung (auch nicht künftig)")
        problem_filter = bar.problem_filter(lambda: apply_filter())

        table = paged_table(route="/metering-points", columns=COLUMNS, rows=[], row_key="id").classes(
            "w-full mt-2"
        )
        # The marker comes from `app.gui.problem_markers`: a table renders
        # its cells as markup while a card rendered elements, so the triangle
        # exists twice and must not drift.
        table.add_slot(
            "body-cell-actions",
            f"""
            <q-td :props="props">
                {TABLE_MARKER_HTML}
                <q-btn dense flat icon="visibility" @click="() => $parent.$emit('view', props.row)" />
                <q-btn dense flat icon="edit" @click="() => $parent.$emit('edit', props.row)" />
                <q-btn dense flat icon="delete" color="negative"
                       @click="() => $parent.$emit('remove', props.row)" />
            </q-td>
            """,
        )

        #: Ids with an open finding, refreshed with the list so a
        #: correction makes the marker disappear.
        problems: dict = {}

        all_rows: list[dict] = []
        visible_rows: list[dict] = []

        def apply_filter() -> None:
            """Filter the currently loaded rows by the search input's value
            and the "Nur ohne Assignment" switch.

            A MeteringPoint counts as "ohne Assignment" here if it has no
            current-or-upcoming Assignment at all (see `_current_person_display`/
            `app.models.assignment.get_relevant_for_metering_point`) -- a
            pre-entered future assignment still counts as assigned, so it
            is deliberately excluded from this filter too.

            Returns:
                None.
            """
            nonlocal visible_rows
            needle = (search_input.value or "").strip().lower()
            visible_rows = [r for r in all_rows if needle in r["_search"]] if needle else list(all_rows)
            if without_assignment_switch.value:
                visible_rows = [r for r in visible_rows if r["person"] == "-"]
            if problem_filter.active:
                visible_rows = [r for r in visible_rows if r["id"] in problems]
            visible_rows = apply_sort(visible_rows, SORT_OPTIONS, sort_select)
            # The whole filtered result goes to the table, which shows a
            # window onto it: the search runs over every entry and the
            # printout holds every filtered row, not the page on screen.
            table.rows = [row | {"has_problem": row["id"] in problems} for row in visible_rows]
            table.update()

        def refresh() -> None:
            """Reload all metering points from the database and re-apply the filter.

            Returns:
                None.
            """
            nonlocal all_rows
            with connection_scope() as connection:
                sites = {s.id: s for s in site_repo.list_all(connection)}
                legs = {leg.id: leg for leg in leg_repo.list_all(connection)}
                all_rows = [
                    _to_row(connection, mp, sites, legs) for mp in metering_point_repo.list_all(connection)
                ]
            problems.clear()
            problems.update(load_problems(SUBJECT_METERING_POINT))
            problem_filter.update(set(problems))
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())
        without_assignment_switch.on_value_change(lambda _: apply_filter())

        def open_form(existing: MeteringPoint | None) -> None:
            """Open the create/edit dialog for a MeteringPoint.

            Args:
                existing: MeteringPoint to edit, or `None` to create a new one.

            Returns:
                None.
            """
            open_metering_point_form(existing=existing, on_saved=lambda _: refresh())

        def on_edit(row: dict) -> None:
            """Card edit-button handler: open the edit dialog for this MeteringPoint.

            Args:
                row: Row dict built by `_to_row`.

            Returns:
                None.
            """
            with connection_scope() as connection:
                existing = metering_point_repo.get(connection, row["id"])
            open_form(existing)

        def on_remove(row: dict) -> None:
            """Card delete-button handler: delete the MeteringPoint after confirmation.

            Args:
                row: Row dict built by `_to_row`.

            Returns:
                None.
            """
            metering_point_id = row["id"]
            designation_text = row["designation"]

            with ui.dialog() as confirm, ui.card():
                ui.label(
                    f'Messpunkt "{designation_text}" wirklich löschen? '
                    "Zugehörige Zuordnungen und Messwerte werden mitgelöscht."
                )
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            metering_point_repo.delete(connection, metering_point_id)
                        confirm.close()
                        # notify before refresh() -- see save() above for why
                        safe_notify("Gelöscht.", type="warning")
                        refresh()

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("view", lambda event: ui.navigate.to(f"/metering-points/{event.args['id']}"))
        table.on("edit", lambda event: on_edit(event.args))
        table.on("remove", lambda event: on_remove(event.args))

        refresh()


@ui.page("/metering-points/{metering_point_id}")
def metering_point_detail_page(metering_point_id: int) -> None:
    """Render one MeteringPoint's detail view: designation, direction,
    site, LEG and currently assigned Person.

    Args:
        metering_point_id: Database id of the MeteringPoint, from the URL path.

    Returns:
        None.
    """
    with connection_scope() as connection:
        mp = metering_point_repo.get(connection, metering_point_id)
        site = site_repo.get(connection, mp.site_id) if mp else None
        leg = leg_repo.get(connection, mp.leg_id) if mp and mp.leg_id else None
        person_name, person_is_future = (
            _current_person_display(connection, metering_point_id) if mp else ("-", False)
        )

    with page_frame("/metering-points", "Messpunkt" if mp is None else mp.designation):
        if mp is None:
            ui.label("Messpunkt nicht gefunden.").classes("text-negative")
            ui.link("← Zurück zu Messpunkten", "/metering-points")
            return

        render_detail_header(
            list_route="/metering-points",
            list_label="Messpunkte",
            title=mp.designation,
            on_edit=lambda: open_metering_point_form(existing=mp, on_saved=lambda _: ui.navigate.reload()),
        )

        # What the triangle in the list withheld: the eye shows it,
        # the pencil fixes it. See `app.gui.problem_markers`.
        render_problem_notes(load_problems(SUBJECT_METERING_POINT).get(mp.id))
        _metering_point_designation_row(mp.designation, classes="text-xl font-bold mt-2")
        with ui.card().classes("w-full max-w-lg"):
            if mp.label:
                ui.label(f"Bezeichnung: {mp.label}")
            ui.label(f"Messrichtung: {DIRECTION_LABELS.get(mp.direction, mp.direction)}")
            if site:
                ui.label(f"Standort: {' '.join(p for p in (site.street, site.house_number) if p)}")
                ui.label(" ".join(p for p in (site.postal_code, site.municipality) if p))
            else:
                ui.label("Standort: ?")
            if site:
                ui.link("Standort ansehen", f"/sites/{site.id}")
            ui.label(f"LEG: {leg.name if leg else '-'}")
            person_detail_label = ui.label(f"Aktuell zugeordnete Person: {person_name}")
            if person_is_future:
                person_detail_label.classes("text-orange-8")
                ui.label("(bevorstehend -- noch nicht gestartet)").classes("text-caption text-orange-8")
            if mp.pv_capacity_kwp is not None:
                ui.label(f"PV-Leistung: {mp.pv_capacity_kwp:g} kWp")
            if mp.battery_capacity_kwh is not None:
                ui.label(f"Batteriespeicher: {mp.battery_capacity_kwh:g} kWh")
