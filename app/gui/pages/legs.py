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

A LEG whose Messpunkte span more than one Trafokreis is flagged here (see
`app.domain.leg_composition`): the grid operator (BKW) only grants the
full same-Trafokreis discount within one Trafokreis, so mixed LEGs
warrant a heads-up to the administrator, e.g. to inform the affected
Personen. If every one of those Trafokreise would also work fine as its
own LEG (see `app.domain.participant_mix.leg_should_split`), the
Trafokreis(e) line is highlighted -- splitting would earn all of them the
better discount instead of today's shared, lower one.
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
from app.models import leg as leg_repo
from app.models.leg import Leg, LegInUseError

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


def _to_row(connection, leg: Leg) -> dict:
    """Convert a `Leg` into a row dict backing both the card and the printout.

    Args:
        connection: Open SQLite connection.
        leg: LEG to convert.

    Returns:
        A dict with the fields required by `PRINT_COLUMNS` and `render_card`,
        plus a hidden `_search` key used for client-side filtering.
    """
    composition = compute_leg_composition(connection, leg.id)
    trafokreis_names = ", ".join(t.name for t in composition.trafokreise) or "-"
    should_split = leg_should_split(connection, leg.id)
    if not composition.trafokreise:
        trafokreise_display = "-"
    elif should_split:
        trafokreise_display = (
            f"🌟 Aufteilen empfehlenswert (alle {len(composition.trafokreise)} Trafokreise "
            f"wären allein grün -- besserer BKW-Rabatt möglich): {trafokreis_names}"
        )
    elif composition.is_mixed:
        trafokreise_display = f"⚠ Mehrere Trafokreise ({len(composition.trafokreise)}): {trafokreis_names}"
    else:
        trafokreise_display = f"✓ Preisoptimiert (1 Trafokreis: {trafokreis_names})"
    mix = compute_participant_mix_for_leg(connection, leg.id)
    search_text = " ".join([leg.name, leg.bemerkung or "", trafokreis_names]).lower()
    return {
        "id": leg.id,
        "name": leg.name,
        "messpunkte_count": leg_repo.count_messpunkte(connection, leg.id),
        "trafokreise": trafokreise_display,
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
                        ui.button(icon="edit", on_click=lambda r=row: on_edit(r)).props("dense flat")
                        ui.button(icon="delete", on_click=lambda r=row: on_remove(r)).props(
                            "dense flat color=negative"
                        )
                ui.label(row["trafokreise"]).classes(
                    "w-full text-body2" + (" bg-amber-3 rounded px-2 py-1" if row["should_split"] else "")
                )
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
                legs = leg_repo.list_all(connection)
                all_rows = [_to_row(connection, leg) for leg in legs]

                # Aggregated per LEG -- a LEG can be the "too spread out"
                # target of more than one Trafokreis's upgrade candidacy
                # (each contributing distinct persons, see
                # app.domain.participant_mix.UpgradeCandidate).
                upgrade_person_counts_by_leg: dict[int, int] = {}
                for candidate in find_upgrade_candidates(connection):
                    for mixed_leg in candidate.mixed_legs:
                        upgrade_person_counts_by_leg[mixed_leg.id] = (
                            upgrade_person_counts_by_leg.get(mixed_leg.id, 0) + candidate.person_count
                        )

                mixed_warnings = []
                for leg in legs:
                    composition = compute_leg_composition(connection, leg.id)
                    if composition.is_mixed:
                        trafokreis_names = ", ".join(t.name for t in composition.trafokreise)
                        mixed_warnings.append(
                            f"⚠ LEG „{leg.name}“ umfasst mehrere Trafokreise "
                            f"({trafokreis_names}) -- die BKW gewährt dafür "
                            "vermutlich einen tieferen Rabatt als innerhalb "
                            "eines einzelnen Trafokreises."
                        )
                    person_count = upgrade_person_counts_by_leg.get(leg.id, 0)
                    if person_count:
                        mixed_warnings.append(
                            f"⭐ {person_count} Person(en) in LEG „{leg.name}“ "
                            "könnten in ein eigenes LEG wechseln -- ihr "
                            "Trafokreis hat inzwischen sowohl Prosumer als "
                            "auch Consumer."
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
                    refresh()
                    ui.notify("Gespeichert.", type="positive")

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
                            ui.notify(str(exc), type="negative")
                            return
                        confirm.close()
                        refresh()
                        ui.notify("Gelöscht.", type="warning")

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        refresh()
