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
banner repeats this above the list; it is visible enough per card. If
every one of those substation areas would also work fine as its own LEG (see
`app.domain.participant_mix.leg_should_split`), the line is highlighted
instead as "🌟 Aufteilen empfehlenswert". A more targeted hint -- which
specific substation area has newly become viable, and how many people could
move -- is shown above the list (see `app.domain.participant_mix.
find_upgrade_candidates`) and, per affected MeteringPoint, as a coloured star
on that LEG's own detail page (`/legs/{id}`, `leg_detail_page`).
"""

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.leg_composition import compute_leg_composition
from app.domain.participant_mix import (
    compute_participant_mix_for_leg,
    find_upgrade_candidates,
    leg_should_split,
)
from app.gui.navigation import page_frame
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
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
    ("Prosumer : Consumer", "prosumer_consumer"),
    ("Bemerkung", "note"),
]


def _mix_badge(mix) -> str:
    """Format a `ParticipantMix` as a coloured "<N> Prosumer : <N> Consumer" badge.

    Args:
        mix: The `app.domain.participant_mix.ParticipantMix` to display.

    Returns:
        A short text badge -- 🟢 if both sides are present, 🔴 if the
        LEG is one-sided (or empty).
    """
    symbol = "🔴" if mix.is_one_sided else "🟢"
    return f"{symbol} {mix.prosumer_count} Prosumer : {mix.consumer_count} Consumer"


def _to_row(connection, leg: Leg, *, min_persons: int) -> dict:
    """Convert a `Leg` into a row dict backing both the card and the printout.

    Args:
        connection: Open SQLite connection.
        leg: LEG to convert.
        min_persons: `LegSettings.leg_founding_min_persons`, passed
            through to `leg_should_split`.

    Returns:
        A dict with the fields required by `PRINT_COLUMNS` and `render_card`,
        plus a hidden `_search` key used for client-side filtering.
    """
    composition = compute_leg_composition(connection, leg.id)
    substation_area_names_list = [t.name for t in composition.substation_areas]
    substation_area_names = ", ".join(substation_area_names_list) or "-"
    should_split = leg_should_split(connection, leg.id, min_persons=min_persons)
    if not composition.substation_areas:
        substation_areas_status = "-"
    elif should_split:
        substation_areas_status = (
            f"🌟 Aufteilen empfehlenswert ({len(substation_area_names_list)} Trafokreise) -- "
            "besserer BKW-Rabatt möglich"
        )
    elif composition.is_mixed:
        substation_areas_status = f"Nicht Preisoptimiert ({len(substation_area_names_list)} Trafokreise)"
    else:
        substation_areas_status = "✓ Preisoptimiert"
    mix = compute_participant_mix_for_leg(connection, leg.id)
    search_text = " ".join([leg.name, leg.note or "", substation_area_names]).lower()
    return {
        "id": leg.id,
        "name": leg.name,
        "metering_points_count": leg_repo.count_metering_points(connection, leg.id),
        # Flattened for the printout/CSV export (a single-cell text), see
        # app.gui.print_list -- the on-screen card uses trafokreise_status/
        # trafokreise_liste instead, to list the substation areas one per line.
        "substation_areas": (
            f"{substation_areas_status}: {substation_area_names}" if composition.substation_areas else substation_areas_status
        ),
        "substation_areas_status": substation_areas_status,
        # Only listed on-screen for a single substation area -- a LEG can span
        # a dozen or more, and the point of this card is a fast overview,
        # not an exhaustive list (the full list of metering points with their
        # substation area is one click away on this LEG's own detail page).
        "substation_areas_list": substation_area_names_list if len(substation_area_names_list) <= 1 else [],
        "prosumer_consumer": _mix_badge(mix),
        "note": leg.note,
        "should_split": should_split,
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
                    rubrik="LEGs",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: visible_rows,
                    get_filter_description=lambda: (
                        f'Suche: "{search_input.value.strip()}"' if search_input.value else None
                    ),
                )
                ui.button("+ Neue LEG", on_click=lambda: open_form(None))

        search_input = ui.input("Suche (Name, Bemerkung, Trafokreis...)").classes(
            "w-full max-w-md"
        ).props("debounce=300 clearable")

        warnings_column = ui.column().classes("w-full")

        list_container = ui.column().classes("w-full gap-2 mt-2")

        all_rows: list[dict] = []
        visible_rows: list[dict] = []

        def render_card(row: dict) -> None:
            """Render one LEG as a card with wrapping field groups.

            Args:
                row: Row dict from `_to_row`.

            Returns:
                None.
            """
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center gap-4 flex-wrap"):
                    ui.label(row["name"]).classes("font-bold")
                    ui.label(f"{row['metering_points_count']} Messpunkt(e)").classes("text-body2")
                    ui.label(row["prosumer_consumer"]).classes("text-body2")
                    with ui.row().classes("gap-1 ml-auto"):
                        ui.button(
                            icon="visibility",
                            on_click=lambda r=row: ui.navigate.to(f"/legs/{r['id']}"),
                        ).props("dense flat")
                        ui.button(icon="edit", on_click=lambda r=row: on_edit(r)).props("dense flat")
                        ui.button(icon="delete", on_click=lambda r=row: on_remove(r)).props(
                            "dense flat color=negative"
                        )
                with ui.column().classes(
                    "w-full gap-0" + (" bg-amber-3 rounded px-2 py-1" if row["should_split"] else "")
                ):
                    ui.label(row["substation_areas_status"]).classes("text-body2")
                    for substation_area_name in row["substation_areas_list"]:
                        ui.label(substation_area_name).classes("text-body2 text-grey-7 ml-4")
                if row["note"]:
                    ui.label(row["note"]).classes("w-full text-body2 text-grey-7")

        def apply_filter() -> None:
            """Filter the currently loaded rows by the search input's value.

            Returns:
                None.
            """
            nonlocal visible_rows
            needle = (search_input.value or "").strip().lower()
            visible_rows = [r for r in all_rows if not needle or needle in r["_search"]]
            list_container.clear()
            with list_container:
                if not visible_rows:
                    ui.label("Keine LEGs gefunden.").classes("text-grey-6")
                for row in visible_rows:
                    render_card(row)

        def refresh() -> None:
            """Reload all LEGs from the database and re-apply the filter.

            Returns:
                None.
            """
            nonlocal all_rows
            with connection_scope() as connection:
                min_persons = settings_repo.get_settings(connection).leg_founding_min_persons
                legs = leg_repo.list_all(connection)
                all_rows = [_to_row(connection, leg, min_persons=min_persons) for leg in legs]

                # Aggregated per LEG, naming each candidate substation area
                # individually -- a LEG can be the "too spread out" target
                # of more than one substation area's upgrade candidacy (see
                # app.domain.participant_mix.UpgradeCandidate), and the
                # point of this hint is to say exactly *which* substation area
                # to found a new LEG for, not just that "some" people could
                # move.
                upgrade_info_by_leg: dict[int, list[tuple[str, int]]] = {}
                for candidate in find_upgrade_candidates(connection, min_persons=min_persons):
                    for mixed_leg in candidate.mixed_legs:
                        upgrade_info_by_leg.setdefault(mixed_leg.id, []).append(
                            (candidate.substation_area.name, candidate.person_count)
                        )

                mixed_warnings = []
                for leg in legs:
                    for substation_area_name, person_count in upgrade_info_by_leg.get(leg.id, []):
                        mixed_warnings.append(
                            f"⭐ Trafokreis „{substation_area_name}“ hat genug Prosumer und "
                            f"Consumer für eine eigene LEG -- {person_count} Person(en) "
                            f"aus „{leg.name}“ könnten dorthin wechseln."
                        )
            apply_filter()

            warnings_column.clear()
            with warnings_column:
                for message in mixed_warnings:
                    ui.label(message).classes("text-warning text-body2")

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
                note = ui.textarea(
                    "Bemerkung (optional)",
                    value=existing.note if existing else "",
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
                    if check_duplicate():
                        error_label.text = "Dieser Name wird bereits verwendet."
                        return
                    try:
                        with connection_scope() as connection:
                            if existing:
                                updated = Leg(
                                    id=existing.id,
                                    name=name.value.strip(),
                                    note=note.value.strip(),
                                    created_at=existing.created_at,
                                )
                                leg_repo.update(connection, updated)
                            else:
                                new_leg = Leg(
                                    id=None,
                                    name=name.value.strip(),
                                    note=note.value.strip(),
                                    created_at="",
                                )
                                leg_repo.create(connection, new_leg)
                    except Exception as exc:  # unique constraint race, etc.
                        error_label.text = f"Fehler beim Speichern: {exc}"
                        return
                    dialog.close()
                    # notify before refresh() -- see app.gui.safe_notify's
                    # module docstring for why a plain ui.notify() here can
                    # raise "parent element ... has been deleted" once the
                    # card this dialog was opened from is gone.
                    safe_notify("Gespeichert.", type="positive")
                    refresh()

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    ui.button("Speichern", on_click=save)
            dialog.open()

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

        refresh()


def _metering_point_row_for_leg(
    mp, sites: dict, substation_areas: dict, upgrade_substation_area_ids: set[int]
) -> dict:
    """Convert one MeteringPoint of a LEG into a row dict for the detail table.

    Args:
        mp: MeteringPoint to convert.
        sites: Preloaded `{site_id: site}` lookup.
        substation areas: Preloaded `{substation_area_id: substation area}` lookup.
        upgrade_substation_area_ids: substation area ids that are upgrade candidates
            for this specific LEG (see `app.domain.participant_mix.
            find_upgrade_candidates` -- filtered by the caller to
            candidates whose `mixed_legs` includes this LEG). A MeteringPoint
            on one of these substation areas is marked with a star: it is one
            of the ones an administrator should move into a new, dedicated
            LEG for that substation area.

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
        "substation_area": substation_area.name if substation_area else "-",
        "is_upgrade_candidate": substation_area is not None and substation_area.id in upgrade_substation_area_ids,
        "leg_id": mp.leg_id,
    }


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
        ui.label(f'LEG ändern für „{row["designation"]}“').classes("text-lg font-bold")
        leg_select = ui.select(
            leg_options, label="LEG", value=row["leg_id"], with_input=True
        ).classes("w-full")
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
    dialog.open()


@ui.page("/legs/{leg_id}")
def leg_detail_page(leg_id: int) -> None:
    """Render one LEG's detail view: its metering points, each with the
    substation area assigned via its site (see `app.models.site`),
    sortable by clicking a column header, plus a quick "LEG ändern"
    action per row.

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

        ui.link("← Zurück zu LEGs", "/legs")
        ui.label(leg.name).classes("text-xl font-bold mt-2")
        if leg.note:
            ui.label(leg.note).classes("text-body2 text-grey-7")

        count_label = ui.label("").classes("text-body2 text-grey-7 mt-2")
        upgrade_hint_column = ui.column().classes("w-full gap-0")

        table = ui.table(
            columns=[
                {
                    "name": "designation", "label": "Messpunkt",
                    "field": "designation", "align": "left", "sortable": True,
                },
                {
                    "name": "direction", "label": "Messrichtung",
                    "field": "direction", "align": "left", "sortable": True,
                },
                {
                    "name": "site_address", "label": "Adresse",
                    "field": "site_address", "align": "left", "sortable": True,
                },
                {
                    "name": "substation_area", "label": "Trafokreis",
                    "field": "substation_area", "align": "left", "sortable": True,
                },
                {"name": "actions", "label": "", "field": "actions", "align": "right"},
            ],
            rows=[],
            row_key="id",
        ).classes("w-full mt-2")
        table.add_slot(
            "body-cell-substation_area",
            r'''
            <q-td :props="props" :class="props.row.is_upgrade_candidate ? 'text-amber-9' : ''">
                <span v-if="props.row.is_upgrade_candidate">⭐ </span>{{ props.value }}
            </q-td>
            ''',
        )
        table.add_slot(
            "body-cell-actions",
            r'''
            <q-td :props="props">
                <q-btn dense flat icon="swap_horiz"
                    @click="() => $parent.$emit('change_leg', props.row)" />
            </q-td>
            ''',
        )

        def refresh_table() -> None:
            """Reload this LEG's metering points (a row disappears once its LEG
            is changed away from this one) and the upgrade-candidate hint.

            Returns:
                None.
            """
            with connection_scope() as inner_connection:
                min_persons = settings_repo.get_settings(inner_connection).leg_founding_min_persons
                sites = {s.id: s for s in site_repo.list_all(inner_connection)}
                substation_areas = {t.id: t for t in substation_area_repo.list_all(inner_connection)}
                metering_points = [
                    mp for mp in metering_point_repo.list_all(inner_connection) if mp.leg_id == leg_id
                ]
                # Which substation area(e), among the ones this LEG spans, could
                # now be split off into their own -- named explicitly
                # rather than just hinting that "some" metering points should
                # move, see the module docstring.
                upgrade_candidates = [
                    c for c in find_upgrade_candidates(inner_connection, min_persons=min_persons)
                    if any(l.id == leg_id for l in c.mixed_legs)
                ]
                upgrade_substation_area_ids = {c.substation_area.id for c in upgrade_candidates}
                table.rows = [
                    _metering_point_row_for_leg(mp, sites, substation_areas, upgrade_substation_area_ids)
                    for mp in metering_points
                ]
            table.update()
            count_label.text = f"{len(table.rows)} Messpunkt(e)"

            upgrade_hint_column.clear()
            with upgrade_hint_column:
                for candidate in upgrade_candidates:
                    ui.label(
                        f"⭐ Trafokreis „{candidate.substation_area.name}“ hat genug Prosumer und "
                        f"Consumer für eine eigene LEG -- {candidate.person_count} Person(en) auf "
                        "den unten markierten Messpunkten könnten dorthin wechseln."
                    ).classes("text-body2 text-amber-9")

        def on_change_leg(event) -> None:
            """Table row action handler: open the "LEG ändern" dialog.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            with connection_scope() as inner_connection:
                leg_options = {l.id: l.name for l in leg_repo.list_all(inner_connection)}
            _open_change_leg_dialog(event.args, leg_options, refresh_table)

        table.on("change_leg", on_change_leg)

        refresh_table()
