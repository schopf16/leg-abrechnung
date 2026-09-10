"""Trafokreise management page: list, search, create, edit, delete.

Rendered as one card per Trafokreis (not a single-row-per-Trafokreis
table): once Bemerkung has any real content, a flat table either forces
horizontal scrolling (wide fixed columns) or, if wrapped, very tall rows
that push everything else below the fold -- neither is acceptable. Cards
let the Bemerkung wrap onto its own full-width line instead, so one entry
takes the 2-3 lines it actually needs and no more (same rationale as
`app.gui.pages.personen`).

A Trafokreis cannot be deleted while Standorte still reference it (see
`app.models.trafokreis.TrafokreisInUseError`). Its `name` must be unique,
checked live as the administrator types.
"""

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.participant_mix import compute_participant_mix_for_trafokreis, find_upgrade_candidates
from app.gui.navigation import page_frame
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.models import settings as settings_repo
from app.models import standort as standort_repo
from app.models import trafokreis as trafokreis_repo
from app.models.trafokreis import Trafokreis, TrafokreisInUseError

#: `(label, field)` pairs for the printed table -- independent of the
#: on-screen card layout, see `app.gui.print_list`.
PRINT_COLUMNS = [
    ("Name", "name"),
    ("BKW-Bezeichnung", "bkw_bezeichnung"),
    ("Standorte", "standorte_count"),
    ("Prosumer : Consumer", "prosumer_consumer"),
    ("Hinweis", "hinweis"),
    ("Bemerkung", "bemerkung"),
]


def _mix_badge(mix) -> str:
    """Format a `ParticipantMix` as a coloured "<N> Prosumer : <N> Consumer" badge.

    Args:
        mix: The `app.domain.participant_mix.ParticipantMix` to display.

    Returns:
        A short text badge -- 🟢 if both sides are present, 🔴 if the
        Trafokreis is one-sided (or empty).
    """
    symbol = "🔴" if mix.ist_einseitig else "🟢"
    return f"{symbol} {mix.prosumer_count} Prosumer : {mix.consumer_count} Consumer"


def _to_row(
    connection, trafokreis: Trafokreis, standort_ids: set[int], upgrade_trafokreis_ids: set[int],
) -> dict:
    """Convert a `Trafokreis` into a row dict backing both the card and the printout.

    Args:
        connection: Open SQLite connection.
        trafokreis: Trafokreis to convert.
        standort_ids: This Trafokreis's own Standort ids (preloaded by the
            caller to avoid re-querying every Standort per row).
        upgrade_trafokreis_ids: Trafokreis ids with LEG-upgrade potential
            (see `app.domain.participant_mix.find_upgrade_candidates`),
            preloaded once for the whole list.

    Returns:
        A dict with the fields required by `PRINT_COLUMNS` and `render_card`,
        plus a hidden `_search` key used for client-side filtering.
    """
    mix = compute_participant_mix_for_trafokreis(connection, trafokreis.id)
    prosumer_consumer = _mix_badge(mix)
    if trafokreis.id in upgrade_trafokreis_ids:
        prosumer_consumer += " ⭐ Potential für eigenes LEG"

    search_text = " ".join(
        [trafokreis.name, trafokreis.bkw_bezeichnung or "", trafokreis.bemerkung or ""]
    ).lower()
    return {
        "id": trafokreis.id,
        "name": trafokreis.name,
        "bkw_bezeichnung": trafokreis.bkw_bezeichnung,
        "standorte_count": len(standort_ids),
        "prosumer_consumer": prosumer_consumer,
        "hinweis": mix.hinweis,
        "bemerkung": trafokreis.bemerkung,
        "_search": search_text,
    }


@ui.page("/trafokreise")
def trafokreise_page() -> None:
    """Render the Trafokreise CRUD page with search.

    Returns:
        None.
    """
    with page_frame("/trafokreise", "Trafokreise"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Ein Trafokreis ist eine Eigenschaft des Standorts, nie einer "
                "Person, eines Messpunkts oder einer LEG direkt. Er entspricht "
                "der physischen Gruppierung durch den Netzbetreiber (BKW). Der "
                "„Name“ ist ein frei wählbarer (Pseudo-)Name; die offizielle "
                "BKW-Nummer gehört ins Feld „BKW-Bezeichnung“."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    rubrik="Trafokreise",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: visible_rows,
                    get_filter_description=lambda: (
                        f'Suche: "{search_input.value.strip()}"' if search_input.value else None
                    ),
                )
                ui.button("+ Neuer Trafokreis", on_click=lambda: open_form(None))

        search_input = ui.input("Suche (Name, BKW-Bezeichnung, Bemerkung...)").classes(
            "w-full max-w-md"
        ).props("debounce=300 clearable")

        list_container = ui.column().classes("w-full gap-2 mt-2")

        all_rows: list[dict] = []
        visible_rows: list[dict] = []

        def render_card(row: dict) -> None:
            """Render one Trafokreis as a card with wrapping field groups.

            Args:
                row: Row dict from `_to_row`.

            Returns:
                None.
            """
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center gap-4 flex-wrap"):
                    with ui.column().classes("gap-0 min-w-[180px]"):
                        ui.label(row["name"]).classes("font-bold")
                        if row["bkw_bezeichnung"]:
                            ui.label(row["bkw_bezeichnung"]).classes("text-caption text-grey-6")
                    ui.label(f"{row['standorte_count']} Standort(e)").classes("text-body2")
                    ui.label(row["prosumer_consumer"]).classes("text-body2")
                    with ui.row().classes("gap-1 ml-auto"):
                        ui.button(icon="edit", on_click=lambda r=row: on_edit(r)).props("dense flat")
                        ui.button(icon="delete", on_click=lambda r=row: on_remove(r)).props(
                            "dense flat color=negative"
                        )
                if row["hinweis"]:
                    ui.label(row["hinweis"]).classes("w-full text-body2 text-negative")
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
                    ui.label("Keine Trafokreise gefunden.").classes("text-grey-6")
                for row in visible_rows:
                    render_card(row)

        def refresh() -> None:
            """Reload all Trafokreise from the database and re-apply the filter.

            Returns:
                None.
            """
            nonlocal all_rows
            with connection_scope() as connection:
                min_personen = settings_repo.get_settings(connection).leg_gruendung_min_personen
                standorte = standort_repo.list_all(connection)
                upgrade_trafokreis_ids = {
                    c.trafokreis.id
                    for c in find_upgrade_candidates(connection, min_personen=min_personen)
                }
                all_rows = [
                    _to_row(
                        connection, trafokreis,
                        {s.id for s in standorte if s.trafokreis_id == trafokreis.id},
                        upgrade_trafokreis_ids,
                    )
                    for trafokreis in trafokreis_repo.list_all(connection)
                ]
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())

        def open_form(existing: Trafokreis | None) -> None:
            """Open the create/edit dialog for a Trafokreis.

            Args:
                existing: Trafokreis to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Trafokreis bearbeiten" if existing else "Neuer Trafokreis").classes(
                    "text-lg font-bold"
                )
                name = ui.input(
                    "Name (frei wählbar, z. B. Pseudo-Name)",
                    value=existing.name if existing else "",
                ).classes("w-full").props("debounce=300")
                duplicate_warning = ui.label("").classes("text-warning")
                bkw_bezeichnung = ui.input(
                    "BKW-Bezeichnung (optional, z. B. TRA21359)",
                    value=existing.bkw_bezeichnung if existing else "",
                ).classes("w-full")
                bemerkung = ui.textarea(
                    "Bemerkung (optional)",
                    value=existing.bemerkung if existing else "",
                ).classes("w-full").props("rows=3")
                error_label = ui.label("").classes("text-negative")

                def check_duplicate() -> bool:
                    """Check whether the current name input is already used by another Trafokreis.

                    Updates `duplicate_warning` as a side effect.

                    Returns:
                        `True` if the name is a duplicate of a different Trafokreis.
                    """
                    typed = name.value.strip()
                    if not typed:
                        duplicate_warning.text = ""
                        return False
                    with connection_scope() as connection:
                        found = trafokreis_repo.get_by_name(connection, typed)
                    is_duplicate = found is not None and (existing is None or found.id != existing.id)
                    duplicate_warning.text = (
                        "Dieser Name wird bereits verwendet." if is_duplicate else ""
                    )
                    return is_duplicate

                name.on_value_change(lambda _: check_duplicate())

                def save() -> None:
                    """Validate the form and persist the Trafokreis.

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
                                updated = Trafokreis(
                                    id=existing.id,
                                    name=name.value.strip(),
                                    bkw_bezeichnung=bkw_bezeichnung.value.strip(),
                                    bemerkung=bemerkung.value.strip(),
                                    created_at=existing.created_at,
                                )
                                trafokreis_repo.update(connection, updated)
                            else:
                                new_trafokreis = Trafokreis(
                                    id=None,
                                    name=name.value.strip(),
                                    bkw_bezeichnung=bkw_bezeichnung.value.strip(),
                                    bemerkung=bemerkung.value.strip(),
                                    created_at="",
                                )
                                trafokreis_repo.create(connection, new_trafokreis)
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
                row: Row dict of the Trafokreis to edit.

            Returns:
                None.
            """
            with connection_scope() as connection:
                existing = trafokreis_repo.get(connection, row["id"])
            open_form(existing)

        def on_remove(row: dict) -> None:
            """Card delete-button handler: delete the Trafokreis after confirmation.

            Args:
                row: Row dict of the Trafokreis to delete.

            Returns:
                None.
            """
            trafokreis_id = row["id"]
            name = row["name"]

            with ui.dialog() as confirm, ui.card():
                ui.label(f'Trafokreis "{name}" wirklich löschen?')
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        try:
                            with connection_scope() as connection:
                                trafokreis_repo.delete(connection, trafokreis_id)
                        except TrafokreisInUseError as exc:
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
