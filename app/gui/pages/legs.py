"""LEGs management page: list, search, create, edit, delete.

Rendered as one card per LEG (not a single-row-per-LEG table): once
note has any real content, a flat table either forces horizontal
scrolling (wide fixed columns) or, if wrapped, very tall rows that push
everything else below the fold -- neither is acceptable. Cards let the
note and the substation area(e) summary each wrap onto their own
full-width line instead, so one entry takes the 2-3 lines it actually
needs and no more (same rationale as `app.gui.pages.persons`).

A LEG cannot be deleted while metering points still reference it (see
`app.models.leg.LegInUseError`). Its `name` must be unique -- by default
it matches the physical substation area its metering points are on, but a LEG can
combine metering points from several substation areas if their owners agree to bill
jointly. The name is also what appears on this LEG's invoices, checked
live as the administrator types.

A LEG whose metering points span more than one substation area is shown as "Nicht
Preisoptimiert" here (see `app.domain.leg_composition`), its substation areas
listed one per line -- the grid operator (BKW) only grants the full
same-substation-area discount within one substation area. No separate warning
banner repeats this above the list; it is visible enough per card.

The app used to go further and recommend which people to move into a new,
dedicated LEG. It no longer does, and `app.domain.participant_mix` says why:
both sides being present says nothing about whether a dedicated LEG would
actually work for them. What the detail page shows instead is the one fact
this database holds -- per metering point, whether its substation area
already has a LEG of its own (🟢, so the row can simply be switched over) or
would need one founded first (🟠). Sorting that list by substation area is
how the administrator decides, and that sort already exists
on that LEG's own detail page (`/legs/{id}`, `leg_detail_page`).
"""

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.leg_composition import compute_leg_composition
from app.domain.participant_mix import (
    compute_participant_mix_for_leg,
)
from app.domain.quality_checks import SUBJECT_LEG
from app.gui.filter_bar import FilterBar
from app.gui.form_dialog import form_guard
from app.gui.leg_form import open_leg_form
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
    render_sort_select,
    sort_description,
    text_key,
)
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.domain.production_capacity import compute_headroom, status_classes
from app.models.leg import Leg, LegInUseError
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN

DIRECTION_LABELS = {
    DIRECTION_CONSUMPTION: "Bezug",
    DIRECTION_FEED_IN: "Einspeisung",
}

#: `(label, field)` pairs for the printed table -- independent of the
#: on-screen card layout, see `app.gui.print_list`.
PRINT_COLUMNS = [
    ("Name", "name"),
    ("Messpunkte", "metering_points_count"),
    ("Trafokreis(e)", "substation_areas"),
    ("Produzent : Konsument", "producer_consumer"),
    ("Produktionsleistung", "production_capacity"),
    ("Bemerkung", "note"),
]


#: What the list shows. The Trafokreise a LEG spans are one cell rather than
#: a list of labels: on a dedicated LEG it is one name, and the pooled one is
#: exactly the case the detail page explains per metering point.
COLUMNS = [
    {"name": "name", "label": "Name", "field": "name", "align": "left"},
    {
        "name": "metering_points_count",
        "label": "Messpunkte",
        "field": "metering_points_count",
        "align": "right",
    },
    {
        "name": "producer_consumer",
        "label": "Produzenten / Konsumenten",
        "field": "producer_consumer",
        "align": "left",
    },
    {
        "name": "production_capacity",
        "label": "Produktionsleistung",
        "field": "production_capacity",
        "align": "left",
    },
    {"name": "substation_areas", "label": "Trafokreise", "field": "substation_areas", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]

#: Orders the LEGs list offers, default first.
SORT_OPTIONS = [
    SortOption("name", "Name", lambda row: text_key(row["name"])),
    SortOption(
        "metering_points_count",
        "Anzahl Messpunkte (meiste zuerst)",
        # Negated rather than `reverse=True`, which would also flip the
        # name tiebreak and list equal counts from Z to A.
        lambda row: (-row["metering_points_count"], text_key(row["name"])),
    ),
    SortOption(
        "optimisation",
        "Preisoptimierung (Handlungsbedarf zuerst)",
        lambda row: (row["optimisation_rank"], text_key(row["name"])),
    ),
]


def _mix_badge(mix) -> str:
    """Format a `ParticipantMix` as a coloured "<N> Produzent : <N> Konsument" badge.

    Args:
        mix: The `app.domain.participant_mix.ParticipantMix` to display.

    Returns:
        A short text badge -- 🟢 if both sides are present, 🔴 if the
        LEG is one-sided (or empty).
    """
    symbol = "🔴" if mix.is_one_sided else "🟢"
    # Metering points, not persons: these two numbers sit beside the
    # metering point count and have to add up against it.
    text = f"{symbol} {mix.producer_metering_points} Produzent : {mix.consumer_metering_points} Konsument"
    if mix.unassigned_metering_points:
        text += f"  ⚠ {mix.unassigned_metering_points} ohne Zuordnung"
    return text


def _to_row(connection, leg: Leg, *, warn_percent: float) -> dict:
    """Convert a `Leg` into a row dict backing both the card and the printout.

    Args:
        connection: Open SQLite connection.
        leg: LEG to convert.
        warn_percent: `LegSettings.production_capacity_warn_percent`, the
            point below which the production capacity counts as tight.

    Returns:
        A dict with the fields required by `PRINT_COLUMNS` and `render_card`,
        plus a hidden `_search` key used for client-side filtering.
    """
    composition = compute_leg_composition(connection, leg.id)
    substation_area_names_list = [t.name for t in composition.substation_areas]
    substation_area_names = ", ".join(substation_area_names_list) or "-"
    # Rank and status text come out of one branch chain on purpose: the
    # "Preisoptimierung (Handlungsbedarf zuerst)" order must never claim
    # something the text next to it contradicts. All three are statements
    # about how this LEG is composed -- no recommendation, see the module
    # docstring.
    if not composition.substation_areas:
        substation_areas_status = "-"
        # A LEG with no metering points yet has nothing to optimise. Last,
        # not with the optimised ones -- "✓ Preisoptimiert" would be a
        # claim about a LEG that has not been configured at all.
        optimisation_rank = 2
    elif composition.is_mixed:
        substation_areas_status = f"Nicht Preisoptimiert ({len(substation_area_names_list)} Trafokreise)"
        optimisation_rank = 0
    else:
        substation_areas_status = "✓ Preisoptimiert"
        optimisation_rank = 1
    headroom = compute_headroom(leg.production_capacity_percent, warn_percent=warn_percent)
    mix = compute_participant_mix_for_leg(connection, leg.id)
    search_text = " ".join([leg.name, leg.note or "", substation_area_names]).lower()
    return {
        "id": leg.id,
        "name": leg.name,
        "metering_points_count": leg_repo.count_metering_points(connection, leg.id),
        # One cell of text, for the printout and -- since the list became a
        # table -- for the screen as well. The card drew a status line with
        # the names indented underneath it, which a row has no room for.
        "substation_areas": (
            f"{substation_areas_status}: {substation_area_names}"
            if composition.substation_areas
            else substation_areas_status
        ),
        "substation_areas_status": substation_areas_status,
        "producer_consumer": _mix_badge(mix),
        "production_capacity": headroom.label
        + (
            f" (Stand {leg.production_capacity_recorded_at})"
            if leg.production_capacity_percent is not None and leg.production_capacity_recorded_at
            else ""
        ),
        "production_capacity_status": headroom.status,
        "note": leg.note,
        "optimisation_rank": optimisation_rank,
        "_search": search_text,
    }


@ui.page("/legs")
def legs_page() -> None:
    """Render the LEGs CRUD page with search.

    Returns:
        None.
    """
    with page_frame("/legs", "LEGs"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Eine LEG wird pro Messpunkt zugewiesen (siehe „Messpunkte“), "
                "nie einer Person oder einem Standort direkt. Lokale "
                "Verteilung findet nur innerhalb derselben LEG statt (siehe "
                "Abrechnung). Der Name erscheint auf den Rechnungen dieser LEG."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    heading="LEGs",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: visible_rows,
                    get_filter_description=lambda: (
                        f'Suche: "{search_input.value.strip()}"' if search_input.value else None
                    ),
                    # Named on its own line: a printout is read away from
                    # the screen, where the order is not self-evident.
                    get_sort_description=lambda: sort_description(SORT_OPTIONS, sort_select),
                )
                ui.button("+ Neue LEG", on_click=lambda: open_form(None))

        bar = FilterBar("/legs")
        search_input = bar.search("Name, Bemerkung, Trafokreis")
        sort_select = bar.sort(SORT_OPTIONS, lambda: apply_filter())
        problem_filter = bar.problem_filter(lambda: apply_filter())

        table = paged_table(route="/legs", columns=COLUMNS, rows=[], row_key="id").classes("w-full mt-2")
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
            """Filter the currently loaded rows by the search input's value.

            Returns:
                None.
            """
            nonlocal visible_rows
            needle = (search_input.value or "").strip().lower()
            visible_rows = [r for r in all_rows if not needle or needle in r["_search"]]
            if problem_filter.active:
                visible_rows = [r for r in visible_rows if r["id"] in problems]
            visible_rows = apply_sort(visible_rows, SORT_OPTIONS, sort_select)
            # The whole filtered result goes to the table, which shows a
            # window onto it: the search runs over every entry and the
            # printout holds every filtered row, not the page on screen.
            table.rows = [row | {"has_problem": row["id"] in problems} for row in visible_rows]
            table.update()

        def refresh() -> None:
            """Reload all LEGs from the database and re-apply the filter.

            Returns:
                None.
            """
            nonlocal all_rows
            with connection_scope() as connection:
                settings = settings_repo.get_settings(connection)
                warn_percent = settings.production_capacity_warn_percent
                legs = leg_repo.list_all(connection)
                all_rows = [_to_row(connection, leg, warn_percent=warn_percent) for leg in legs]
            problems.clear()
            problems.update(load_problems(SUBJECT_LEG))
            problem_filter.update(set(problems))
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())

        def open_form(existing: Leg | None) -> None:
            """Open the create/edit dialog for a LEG.

            Args:
                existing: LEG to edit, or `None` to create a new one.

            Returns:
                None.
            """
            open_leg_form(existing=existing, on_saved=lambda _: refresh())

        def on_edit(row: dict) -> None:
            """Card edit-button handler: open the edit dialog for this row.

            Args:
                row: Row dict of the LEG to edit.

            Returns:
                None.
            """
            with connection_scope() as connection:
                existing = leg_repo.get(connection, row["id"])
            open_form(existing)

        def on_remove(row: dict) -> None:
            """Card delete-button handler: delete the LEG after confirmation.

            Args:
                row: Row dict of the LEG to delete.

            Returns:
                None.
            """
            leg_id = row["id"]
            name = row["name"]

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
                            safe_notify(str(exc), type="negative")
                            return
                        confirm.close()
                        # notify before refresh() -- see save() above for why
                        safe_notify("Gelöscht.", type="warning")
                        refresh()

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("view", lambda event: ui.navigate.to(f"/legs/{event.args['id']}"))
        table.on("edit", lambda event: on_edit(event.args))
        table.on("remove", lambda event: on_remove(event.args))

        refresh()


def _dedicated_leg_names(connection, *, exclude_leg_id: int) -> dict[int, str]:
    """Map each substation area to the LEG that covers it alone, if any.

    A LEG "belongs to" a substation area when its metering points sit in
    that one substation area and nowhere else -- which is exactly the
    arrangement BKW grants the full discount for (see
    `app.domain.leg_composition`).

    Args:
        connection: Open SQLite connection.
        exclude_leg_id: The LEG being looked at. Excluded so a
            single-substation-area LEG does not report itself as the
            destination for its own metering points.

    Returns:
        `{substation_area_id: leg_name}`. A substation area absent from
        this mapping has no dedicated LEG yet -- one would have to be
        founded before its metering points could move.
    """
    names: dict[int, str] = {}
    for leg in leg_repo.list_all(connection):
        if leg.id == exclude_leg_id:
            continue
        composition = compute_leg_composition(connection, leg.id)
        if len(composition.substation_areas) == 1:
            names[composition.substation_areas[0].id] = leg.name
    return names


def _leg_spans_several_substation_areas(leg_id: int) -> bool:
    """Whether one LEG spans more than one substation area.

    Opens its own connection because the caller (`leg_detail_page`) needs
    the answer while building the page's layout, before its own
    `refresh_table` connection exists.

    Args:
        leg_id: The LEG to check.

    Returns:
        `True` if this LEG covers several substation areas.
    """
    with connection_scope() as connection:
        return compute_leg_composition(connection, leg_id).is_mixed


def _metering_point_row_for_leg(mp, sites: dict, substation_areas: dict, dedicated_leg_names: dict) -> dict:
    """Convert one MeteringPoint of a LEG into a row dict for the detail table.

    Args:
        mp: MeteringPoint to convert.
        sites: Preloaded `{site_id: site}` lookup.
        substation areas: Preloaded `{substation_area_id: substation area}` lookup.
        dedicated_leg_names: `{substation_area_id: leg_name}` for every
            substation area that already has a LEG of its own (see
            `leg_detail_page`). Decides this row's marker: 🟢 with that
            name means the metering point can be switched straight over,
            🟠 means such a LEG would have to be founded first. Purely a
            statement of fact -- whether moving it is a good idea is the
            administrator's call, see the module docstring.

    Returns:
        A dict with the fields required by `leg_detail_page`'s table.
    """
    site = sites.get(mp.site_id)
    substation_area = (
        substation_areas.get(site.substation_area_id) if site and site.substation_area_id else None
    )
    return {
        "id": mp.id,
        "designation": mp.designation,
        "direction": DIRECTION_LABELS.get(mp.direction, mp.direction),
        "site_address": site.full_address if site else "?",
        # Kept unformatted alongside the display field so DETAIL_SORT_OPTIONS
        # can order the house number numerically ("9" before "68").
        "_site_street": site.street if site else "",
        "_site_house_number": site.house_number if site else "",
        "substation_area": substation_area.name if substation_area else "-",
        # Empty for a site with no substation area recorded: the question
        # "does this substation area have its own LEG" does not arise.
        "dedicated_leg": (
            dedicated_leg_names.get(substation_area.id, "") if substation_area is not None else ""
        ),
        "has_substation_area": substation_area is not None,
        "leg_id": mp.leg_id,
    }


#: Orders the LEG detail page's metering point list offers, default first.
DETAIL_SORT_OPTIONS = [
    SortOption("designation", "Messpunkt", lambda row: text_key(row["designation"])),
    SortOption(
        "site_address",
        "Adresse",
        lambda row: (
            address_key(row["_site_street"], row["_site_house_number"]),
            text_key(row["designation"]),
        ),
    ),
    SortOption(
        "substation_area",
        "Trafokreis",
        lambda row: (text_key(row["substation_area"]), text_key(row["designation"])),
    ),
    SortOption(
        "direction",
        "Messrichtung",
        lambda row: (text_key(row["direction"]), text_key(row["designation"])),
    ),
    SortOption(
        "dedicated_leg",
        "Eigenes LEG vorhanden",
        # Rows still needing a LEG founded first come last: the ones that
        # can be switched over right now are the ones worth looking at.
        lambda row: (
            not row["dedicated_leg"],
            text_key(row["substation_area"]),
            text_key(row["designation"]),
        ),
    ),
]


def _open_change_leg_dialog(row: dict, leg_options: dict[int, str], on_saved) -> None:
    """Open a minimal dialog to reassign one MeteringPoint's LEG.

    Deliberately just the LEG field -- not the full `app.gui.
    metering_point_form`, which also edits designation/site/PV data not
    relevant here. This is the fast path for splitting a few people out
    of a LEG that spans several substation areas, right from that LEG's own
    detail view, instead of looking each MeteringPoint up individually on the
    metering points page.

    Args:
        row: Row dict from `_metering_point_row_for_leg` (needs `id`,
            `designation`, `leg_id`).
        leg_options: `{leg_id: name}` for every LEG, for the select.
        on_saved: Called (no arguments) after a successful save, dialog
            already closed -- typically the caller's own table refresh.

    Returns:
        None.
    """
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-sm"):
        ui.label(f"LEG ändern für „{row['designation']}“").classes("text-lg font-bold")
        leg_select = ui.select(leg_options, label="LEG", value=row["leg_id"], with_input=True).classes(
            "w-full"
        )
        error_label = ui.label("").classes("text-negative")

        def save() -> None:
            """Persist the new LEG assignment for this one MeteringPoint.

            Returns:
                None.
            """
            try:
                with connection_scope() as connection:
                    mp = metering_point_repo.get(connection, row["id"])
                    mp.leg_id = leg_select.value
                    metering_point_repo.update(connection, mp)
            except Exception as exc:  # unique constraint race, etc.
                error_label.text = f"Fehler beim Speichern: {exc}"
                return
            dialog.close()
            # notify before on_saved() -- see app.gui.safe_notify's module
            # docstring for why a plain ui.notify() here can raise "parent
            # element ... has been deleted" once the dialog is gone.
            safe_notify("LEG geändert.", type="positive")
            on_saved()

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            ui.button("Speichern", on_click=save)
    form_guard(dialog, on_save=save)
    dialog.open()


@ui.page("/legs/{leg_id}")
def leg_detail_page(leg_id: int) -> None:
    """Render one LEG's detail view: its metering points, each with the
    substation area assigned via its site (see `app.models.site`), sorted
    through the same "Sortierung" select as every other list (see
    `app.gui.sorting`), plus a quick "LEG ändern" action per row.

    Args:
        leg_id: Database id of the LEG, from the URL path.

    Returns:
        None.
    """
    with connection_scope() as connection:
        leg = leg_repo.get(connection, leg_id)

    with page_frame("/legs", "LEG" if leg is None else leg.name):
        if leg is None:
            ui.label("LEG nicht gefunden.").classes("text-negative")
            ui.link("← Zurück zu LEGs", "/legs")
            return

        render_detail_header(
            list_route="/legs",
            list_label="LEGs",
            title=leg.name,
            on_edit=lambda: open_leg_form(existing=leg, on_saved=lambda _: ui.navigate.reload()),
        )

        # What the triangle in the list withheld: the eye shows it,
        # the pencil fixes it. See `app.gui.problem_markers`.
        render_problem_notes(load_problems(SUBJECT_LEG).get(leg.id))
        ui.label(leg.name).classes("text-xl font-bold mt-2")
        if leg.note:
            ui.label(leg.note).classes("text-body2 text-grey-7")
        # Same colouring as the overview card, so the same figure does not
        # look different depending on where it is read.
        with connection_scope() as settings_connection:
            warn_percent = settings_repo.get_settings(settings_connection).production_capacity_warn_percent
        headroom = compute_headroom(leg.production_capacity_percent, warn_percent=warn_percent)
        ui.label(
            headroom.label
            + (
                f" (Stand {leg.production_capacity_recorded_at})"
                if leg.production_capacity_percent is not None and leg.production_capacity_recorded_at
                else ""
            )
        ).classes("text-body2 " + status_classes(headroom.status))

        count_label = ui.label("").classes("text-body2 text-grey-7 mt-2")

        # Only meaningful on a LEG spanning several substation areas. On a
        # single-substation-area LEG the "own LEG" for that substation area
        # is this one, so every row would be green and say nothing.
        show_dedicated_column = _leg_spans_several_substation_areas(leg_id)
        if show_dedicated_column:
            ui.label(
                "🟢 Für diesen Trafokreis besteht schon ein eigenes LEG -- der Messpunkt "
                "kann direkt dorthin umgestellt werden.    "
                "🟠 Für diesen Trafokreis gibt es noch kein eigenes LEG -- es müsste "
                "zuerst gegründet werden."
            ).classes("text-caption text-grey-7 w-full")

        detail_sort_select = render_sort_select(DETAIL_SORT_OPTIONS, lambda: refresh_table())

        table = ui.table(
            columns=[
                {"name": "designation", "label": "Messpunkt", "field": "designation", "align": "left"},
                {"name": "direction", "label": "Messrichtung", "field": "direction", "align": "left"},
                {"name": "site_address", "label": "Adresse", "field": "site_address", "align": "left"},
                {
                    "name": "substation_area",
                    "label": "Trafokreis",
                    "field": "substation_area",
                    "align": "left",
                },
                *(
                    [
                        {
                            "name": "dedicated_leg",
                            "label": "Eigenes LEG",
                            "field": "dedicated_leg",
                            "align": "left",
                        }
                    ]
                    if show_dedicated_column
                    else []
                ),
                {"name": "actions", "label": "", "field": "actions", "align": "right"},
            ],
            rows=[],
            row_key="id",
        ).classes("w-full mt-2")
        if show_dedicated_column:
            # The dot carries the state, the LEG name beside it saves a
            # lookup for whoever is about to switch the row over.
            table.add_slot(
                "body-cell-dedicated_leg",
                r"""
                <q-td :props="props">
                    <span v-if="!props.row.has_substation_area" class="text-grey-6">–</span>
                    <span v-else-if="props.value">🟢 {{ props.value }}</span>
                    <span v-else>🟠 noch keines</span>
                </q-td>
                """,
            )
        table.add_slot(
            "body-cell-actions",
            r"""
            <q-td :props="props">
                <q-btn dense flat icon="swap_horiz"
                    @click="() => $parent.$emit('change_leg', props.row)" />
            </q-td>
            """,
        )

        def refresh_table() -> None:
            """Reload this LEG's metering points (a row disappears once its LEG
            is changed away from this one).

            Returns:
                None.
            """
            with connection_scope() as inner_connection:
                sites = {s.id: s for s in site_repo.list_all(inner_connection)}
                substation_areas = {t.id: t for t in substation_area_repo.list_all(inner_connection)}
                metering_points = [
                    mp for mp in metering_point_repo.list_all(inner_connection) if mp.leg_id == leg_id
                ]
                dedicated = _dedicated_leg_names(inner_connection, exclude_leg_id=leg_id)
                table.rows = apply_sort(
                    [
                        _metering_point_row_for_leg(mp, sites, substation_areas, dedicated)
                        for mp in metering_points
                    ],
                    DETAIL_SORT_OPTIONS,
                    detail_sort_select,
                )
            table.update()
            count_label.text = f"{len(table.rows)} Messpunkt(e)"

        def on_change_leg(event) -> None:
            """Table row action handler: open the "LEG ändern" dialog.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            with connection_scope() as inner_connection:
                leg_options = {leg.id: leg.name for leg in leg_repo.list_all(inner_connection)}
            _open_change_leg_dialog(event.args, leg_options, refresh_table)

        table.on("change_leg", on_change_leg)

        refresh_table()
