"""LEGs management page: list, search, create, edit, delete.

Rendered as one card per LEG (not a single-row-per-LEG table): once
Bemerkung has any real content, a flat table either forces horizontal
scrolling (wide fixed columns) or, if wrapped, very tall rows that push
everything else below the fold -- neither is acceptable. Cards let the
Bemerkung and the Trafokreis(e) summary each wrap onto their own
full-width line instead, so one entry takes the 2-3 lines it actually
needs and no more (same rationale as `app.gui.pages.personen`).

A LEG cannot be deleted while Messpunkte still reference it (see
`app.models.leg.LegInUseError`). Its `name` must be unique -- by default
it matches the physical Trafokreis its Messpunkte are on, but a LEG can
combine Messpunkte from several Trafokreise if their owners agree to bill
jointly. The name is also what appears on this LEG's invoices, checked
live as the administrator types.

A LEG whose Messpunkte span more than one Trafokreis is shown as "Nicht
Preisoptimiert" here (see `app.domain.leg_composition`), its Trafokreise
listed one per line -- the grid operator (BKW) only grants the full
same-Trafokreis discount within one Trafokreis. No separate warning
banner repeats this above the list; it is visible enough per card. If
every one of those Trafokreise would also work fine as its own LEG (see
`app.domain.participant_mix.leg_should_split`), the line is highlighted
instead as "🌟 Aufteilen empfehlenswert". A more targeted hint -- which
specific Trafokreis has newly become viable, and how many people could
move -- is shown above the list (see `app.domain.participant_mix.
find_upgrade_candidates`) and, per affected Messpunkt, as a coloured star
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
from app.models import messpunkt as messpunkt_repo
from app.models import settings as settings_repo
from app.models import standort as standort_repo
from app.models import trafokreis as trafokreis_repo
from app.models.leg import Leg, LegInUseError
from app.models.messpunkt import MESSRICHTUNG_BEZUG, MESSRICHTUNG_EINSPEISUNG

MESSRICHTUNG_LABELS = {
    MESSRICHTUNG_BEZUG: "Bezug",
    MESSRICHTUNG_EINSPEISUNG: "Einspeisung",
}

#: `(label, field)` pairs for the printed table -- independent of the
#: on-screen card layout, see `app.gui.print_list`.
PRINT_COLUMNS = [
    ("Name", "name"),
    ("Messpunkte", "messpunkte_count"),
    ("Trafokreis(e)", "trafokreise"),
    ("Prosumer : Consumer", "prosumer_consumer"),
    ("Bemerkung", "bemerkung"),
]


def _mix_badge(mix) -> str:
    """Format a `ParticipantMix` as a coloured "<N> Prosumer : <N> Consumer" badge.

    Args:
        mix: The `app.domain.participant_mix.ParticipantMix` to display.

    Returns:
        A short text badge -- 🟢 if both sides are present, 🔴 if the
        LEG is one-sided (or empty).
    """
    symbol = "🔴" if mix.ist_einseitig else "🟢"
    return f"{symbol} {mix.prosumer_count} Prosumer : {mix.consumer_count} Consumer"


def _to_row(connection, leg: Leg, *, min_personen: int) -> dict:
    """Convert a `Leg` into a row dict backing both the card and the printout.

    Args:
        connection: Open SQLite connection.
        leg: LEG to convert.
        min_personen: `LegSettings.leg_gruendung_min_personen`, passed
            through to `leg_should_split`.

    Returns:
        A dict with the fields required by `PRINT_COLUMNS` and `render_card`,
        plus a hidden `_search` key used for client-side filtering.
    """
    composition = compute_leg_composition(connection, leg.id)
    trafokreis_names_liste = [t.name for t in composition.trafokreise]
    trafokreis_names = ", ".join(trafokreis_names_liste) or "-"
    should_split = leg_should_split(connection, leg.id, min_personen=min_personen)
    if not composition.trafokreise:
        trafokreise_status = "-"
    elif should_split:
        trafokreise_status = (
            f"🌟 Aufteilen empfehlenswert ({len(trafokreis_names_liste)} Trafokreise) -- "
            "besserer BKW-Rabatt möglich"
        )
    elif composition.is_mixed:
        trafokreise_status = f"Nicht Preisoptimiert ({len(trafokreis_names_liste)} Trafokreise)"
    else:
        trafokreise_status = "✓ Preisoptimiert"
    mix = compute_participant_mix_for_leg(connection, leg.id)
    search_text = " ".join([leg.name, leg.bemerkung or "", trafokreis_names]).lower()
    return {
        "id": leg.id,
        "name": leg.name,
        "messpunkte_count": leg_repo.count_messpunkte(connection, leg.id),
        # Flattened for the printout/CSV export (a single-cell text), see
        # app.gui.print_list -- the on-screen card uses trafokreise_status/
        # trafokreise_liste instead, to list the Trafokreise one per line.
        "trafokreise": (
            f"{trafokreise_status}: {trafokreis_names}" if composition.trafokreise else trafokreise_status
        ),
        "trafokreise_status": trafokreise_status,
        # Only listed on-screen for a single Trafokreis -- a LEG can span
        # a dozen or more, and the point of this card is a fast overview,
        # not an exhaustive list (the full list of Messpunkte with their
        # Trafokreis is one click away on this LEG's own detail page).
        "trafokreise_liste": trafokreis_names_liste if len(trafokreis_names_liste) <= 1 else [],
        "prosumer_consumer": _mix_badge(mix),
        "bemerkung": leg.bemerkung,
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
                    ui.label(f"{row['messpunkte_count']} Messpunkt(e)").classes("text-body2")
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
                    ui.label(row["trafokreise_status"]).classes("text-body2")
                    for trafokreis_name in row["trafokreise_liste"]:
                        ui.label(trafokreis_name).classes("text-body2 text-grey-7 ml-4")
                if row["bemerkung"]:
                    ui.label(row["bemerkung"]).classes("w-full text-body2 text-grey-7")

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
                min_personen = settings_repo.get_settings(connection).leg_gruendung_min_personen
                legs = leg_repo.list_all(connection)
                all_rows = [_to_row(connection, leg, min_personen=min_personen) for leg in legs]

                # Aggregated per LEG, naming each candidate Trafokreis
                # individually -- a LEG can be the "too spread out" target
                # of more than one Trafokreis's upgrade candidacy (see
                # app.domain.participant_mix.UpgradeCandidate), and the
                # point of this hint is to say exactly *which* Trafokreis
                # to found a new LEG for, not just that "some" people could
                # move.
                upgrade_info_by_leg: dict[int, list[tuple[str, int]]] = {}
                for candidate in find_upgrade_candidates(connection, min_personen=min_personen):
                    for mixed_leg in candidate.mixed_legs:
                        upgrade_info_by_leg.setdefault(mixed_leg.id, []).append(
                            (candidate.trafokreis.name, candidate.person_count)
                        )

                mixed_warnings = []
                for leg in legs:
                    for trafokreis_name, person_count in upgrade_info_by_leg.get(leg.id, []):
                        mixed_warnings.append(
                            f"⭐ Trafokreis „{trafokreis_name}“ hat genug Prosumer und "
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
                    if check_duplicate():
                        error_label.text = "Dieser Name wird bereits verwendet."
                        return
                    try:
                        with connection_scope() as connection:
                            if existing:
                                updated = Leg(
                                    id=existing.id,
                                    name=name.value.strip(),
                                    bemerkung=bemerkung.value.strip(),
                                    created_at=existing.created_at,
                                )
                                leg_repo.update(connection, updated)
                            else:
                                new_leg = Leg(
                                    id=None,
                                    name=name.value.strip(),
                                    bemerkung=bemerkung.value.strip(),
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


def _messpunkt_row_for_leg(
    mp, standorte: dict, trafokreise: dict, upgrade_trafokreis_ids: set[int]
) -> dict:
    """Convert one Messpunkt of a LEG into a row dict for the detail table.

    Args:
        mp: Messpunkt to convert.
        standorte: Preloaded `{standort_id: Standort}` lookup.
        trafokreise: Preloaded `{trafokreis_id: Trafokreis}` lookup.
        upgrade_trafokreis_ids: Trafokreis ids that are upgrade candidates
            for this specific LEG (see `app.domain.participant_mix.
            find_upgrade_candidates` -- filtered by the caller to
            candidates whose `mixed_legs` includes this LEG). A Messpunkt
            on one of these Trafokreise is marked with a star: it is one
            of the ones an administrator should move into a new, dedicated
            LEG for that Trafokreis.

    Returns:
        A dict with the fields required by `leg_detail_page`'s table.
    """
    standort = standorte.get(mp.standort_id)
    trafokreis = (
        trafokreise.get(standort.trafokreis_id) if standort and standort.trafokreis_id else None
    )
    return {
        "id": mp.id,
        "messpunkt_bezeichnung": mp.messpunkt_bezeichnung,
        "messrichtung": MESSRICHTUNG_LABELS.get(mp.messrichtung, mp.messrichtung),
        "standort_adresse": standort.adresse_vollstaendig if standort else "?",
        "trafokreis": trafokreis.name if trafokreis else "-",
        "is_upgrade_candidate": trafokreis is not None and trafokreis.id in upgrade_trafokreis_ids,
        "leg_id": mp.leg_id,
    }


def _open_change_leg_dialog(row: dict, leg_options: dict[int, str], on_saved) -> None:
    """Open a minimal dialog to reassign one Messpunkt's LEG.

    Deliberately just the LEG field -- not the full `app.gui.
    messpunkt_form`, which also edits Bezeichnung/Standort/PV data not
    relevant here. This is the fast path for splitting a few people out
    of a LEG that spans several Trafokreise, right from that LEG's own
    detail view, instead of looking each Messpunkt up individually on the
    Messpunkte page.

    Args:
        row: Row dict from `_messpunkt_row_for_leg` (needs `id`,
            `messpunkt_bezeichnung`, `leg_id`).
        leg_options: `{leg_id: name}` for every LEG, for the select.
        on_saved: Called (no arguments) after a successful save, dialog
            already closed -- typically the caller's own table refresh.

    Returns:
        None.
    """
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-sm"):
        ui.label(f'LEG ändern für „{row["messpunkt_bezeichnung"]}“').classes("text-lg font-bold")
        leg_select = ui.select(
            leg_options, label="LEG", value=row["leg_id"], with_input=True
        ).classes("w-full")
        error_label = ui.label("").classes("text-negative")

        def save() -> None:
            """Persist the new LEG assignment for this one Messpunkt.

            Returns:
                None.
            """
            try:
                with connection_scope() as connection:
                    mp = messpunkt_repo.get(connection, row["id"])
                    mp.leg_id = leg_select.value
                    messpunkt_repo.update(connection, mp)
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
    """Render one LEG's detail view: its Messpunkte, each with the
    Trafokreis assigned via its Standort (see `app.models.standort`),
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
        if leg.bemerkung:
            ui.label(leg.bemerkung).classes("text-body2 text-grey-7")

        count_label = ui.label("").classes("text-body2 text-grey-7 mt-2")
        upgrade_hint_column = ui.column().classes("w-full gap-0")

        table = ui.table(
            columns=[
                {
                    "name": "messpunkt_bezeichnung", "label": "Messpunkt",
                    "field": "messpunkt_bezeichnung", "align": "left", "sortable": True,
                },
                {
                    "name": "messrichtung", "label": "Messrichtung",
                    "field": "messrichtung", "align": "left", "sortable": True,
                },
                {
                    "name": "standort_adresse", "label": "Adresse",
                    "field": "standort_adresse", "align": "left", "sortable": True,
                },
                {
                    "name": "trafokreis", "label": "Trafokreis",
                    "field": "trafokreis", "align": "left", "sortable": True,
                },
                {"name": "actions", "label": "", "field": "actions", "align": "right"},
            ],
            rows=[],
            row_key="id",
        ).classes("w-full mt-2")
        table.add_slot(
            "body-cell-trafokreis",
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
            """Reload this LEG's Messpunkte (a row disappears once its LEG
            is changed away from this one) and the upgrade-candidate hint.

            Returns:
                None.
            """
            with connection_scope() as inner_connection:
                min_personen = settings_repo.get_settings(inner_connection).leg_gruendung_min_personen
                standorte = {s.id: s for s in standort_repo.list_all(inner_connection)}
                trafokreise = {t.id: t for t in trafokreis_repo.list_all(inner_connection)}
                messpunkte = [
                    mp for mp in messpunkt_repo.list_all(inner_connection) if mp.leg_id == leg_id
                ]
                # Which Trafokreis(e), among the ones this LEG spans, could
                # now be split off into their own -- named explicitly
                # rather than just hinting that "some" Messpunkte should
                # move, see the module docstring.
                upgrade_candidates = [
                    c for c in find_upgrade_candidates(inner_connection, min_personen=min_personen)
                    if any(l.id == leg_id for l in c.mixed_legs)
                ]
                upgrade_trafokreis_ids = {c.trafokreis.id for c in upgrade_candidates}
                table.rows = [
                    _messpunkt_row_for_leg(mp, standorte, trafokreise, upgrade_trafokreis_ids)
                    for mp in messpunkte
                ]
            table.update()
            count_label.text = f"{len(table.rows)} Messpunkt(e)"

            upgrade_hint_column.clear()
            with upgrade_hint_column:
                for candidate in upgrade_candidates:
                    ui.label(
                        f"⭐ Trafokreis „{candidate.trafokreis.name}“ hat genug Prosumer und "
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
