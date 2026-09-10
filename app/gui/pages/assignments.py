"""MeteringPoint-to-Person assignment history page (assignments).

Rendered as one card per MeteringPoint (grouping its assignments together)
rather than a flat table: edit/delete buttons are bound directly to Python
callbacks (not via a JS-emit round trip through a Quasar table slot),
which is both more robust to click on and groups related entries more
usefully than one row per Assignment in isolation.
"""

from datetime import date, datetime
from typing import Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.leg_composition import compute_leg_composition
from app.gui.navigation import page_frame
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import assignment as assignment_repo
from app.models.assignment import Assignment


#: `(label, field)` pairs for the printed table -- one row per Assignment,
#: flattened out of the on-screen per-MeteringPoint card grouping.
PRINT_COLUMNS = [
    ("Messpunkt", "metering_point"),
    ("Person", "person_name"),
    ("Gültig von", "valid_from"),
    ("Gültig bis", "valid_to"),
]


def _parse_date(value: str) -> Optional[date]:
    """Parse a date string from a NiceGUI date input into a `date`.

    Args:
        value: Date string in ISO format ("YYYY-MM-DD"), or empty/`None`.

    Returns:
        The parsed `date`, or `None` if `value` is empty.
    """
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


@ui.page("/assignments")
def assignments_page() -> None:
    """Render the assignments CRUD page, including consistency warnings.

    Returns:
        None.
    """
    with page_frame("/assignments", "Zuordnungen"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Legt fest, welcher Person ein Messpunkt in welchem Zeitraum "
                "zugeordnet ist. Bei einem Umzug mitten im Quartal zwei "
                "Zuordnungen mit passendem Enddatum/Startdatum anlegen."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    rubrik="Zuordnungen",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: print_rows,
                )
                ui.button("+ Neue Zuordnung", on_click=lambda: open_form(None))

        warnings_column = ui.column().classes("w-full")
        list_container = ui.column().classes("w-full gap-2 mt-2")

        print_rows: list[dict] = []

        def render_group(metering_point_label: str, group: list[dict]) -> None:
            """Render one MeteringPoint's card with all of its assignments.

            Args:
                metering_point_label: Display label for the MeteringPoint heading.
                group: Row dicts (see `refresh`) belonging to that MeteringPoint,
                    already sorted by `valid_from`.

            Returns:
                None.
            """
            with ui.card().classes("w-full"):
                ui.label(metering_point_label).classes("font-bold")
                for row in group:
                    with ui.row().classes("w-full items-center gap-4 flex-wrap"):
                        ui.label(row["person_name"]).classes("min-w-[180px]")
                        ui.label(f"ab {row['valid_from']}").classes("min-w-[120px] text-grey-7")
                        ui.label(f"bis {row['valid_to']}").classes("min-w-[120px] text-grey-7")
                        with ui.row().classes("gap-1 ml-auto"):
                            ui.button(
                                icon="edit", on_click=lambda z=row["assignment"]: on_edit(z)
                            ).props("dense flat")
                            ui.button(
                                icon="delete",
                                on_click=lambda z=row["assignment"]: on_remove(z),
                            ).props("dense flat color=negative")

        def refresh() -> None:
            """Reload the assignments list (grouped by MeteringPoint) and
            recompute consistency warnings.

            Returns:
                None.
            """
            nonlocal print_rows
            with connection_scope() as connection:
                metering_points = {mp.id: mp for mp in metering_point_repo.list_all(connection)}
                sites = {s.id: s for s in site_repo.list_all(connection)}
                persons = {p.id: p for p in person_repo.list_all(connection)}
                assignments = assignment_repo.list_all(connection)
                all_warnings = []
                for metering_point_id in metering_points:
                    all_warnings.extend(assignment_repo.find_warnings(connection, metering_point_id))

            groups: dict[int, list[dict]] = {}
            for z in assignments:
                groups.setdefault(z.metering_point_id, []).append(
                    {
                        "assignment": z,
                        "person_name": persons[z.person_id].display_name
                        if z.person_id in persons
                        else "?",
                        "valid_from": z.valid_from.isoformat(),
                        "valid_to": z.valid_to.isoformat() if z.valid_to else "offen",
                    }
                )

            print_rows = []
            list_container.clear()
            with list_container:
                if not groups:
                    ui.label("Noch keine Zuordnungen erfasst.")
                for metering_point_id, group in groups.items():
                    mp = metering_points.get(metering_point_id)
                    if mp is None:
                        label = f"Messpunkt #{metering_point_id}"
                    else:
                        site = sites.get(mp.site_id)
                        site_text = site.full_address if site else "?"
                        label = f"{mp.designation} — {site_text}"
                    render_group(label, group)
                    for row in group:
                        print_rows.append(
                            {
                                "metering_point": label,
                                "person_name": row["person_name"],
                                "valid_from": row["valid_from"],
                                "valid_to": row["valid_to"],
                            }
                        )

            warnings_column.clear()
            with warnings_column:
                for warning in all_warnings:
                    ui.label(f"⚠ {warning.message}").classes(
                        "text-negative text-body2"
                    )

        def open_form(existing: Optional[Assignment]) -> None:
            """Open the create/edit dialog for a Assignment.

            Args:
                existing: Assignment to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with connection_scope() as connection:
                metering_points = metering_point_repo.list_all(connection)
                sites = site_repo.list_all(connection)
                persons = person_repo.list_all(connection)
            metering_points_by_id = {mp.id: mp for mp in metering_points}
            site_options = {s.id: s.full_address for s in sites}

            def metering_point_options_for(site_id: Optional[int]) -> dict:
                """Build the MeteringPoint dropdown options, optionally filtered by site.

                Args:
                    site_id: If set, only metering points at that site
                        are included; `None` includes all of them.

                Returns:
                    A `{metering_point_id: label}` dict for `ui.select`.
                """
                return {
                    mp.id: f"{mp.designation} ({'Bezug' if mp.is_consumption else 'Einspeisung'})"
                    for mp in metering_points
                    if site_id is None or mp.site_id == site_id
                }

            # Deactivated persons are hidden from selection for new assignments,
            # but stay selectable when editing a Assignment that already points
            # at one (see app.models.person.delete).
            selectable_persons = [
                p for p in persons if p.active or (existing and p.id == existing.person_id)
            ]
            person_options = {
                p.id: p.display_name + ("" if p.active else " (inaktiv)") for p in selectable_persons
            }

            initial_site_id = (
                metering_points_by_id[existing.metering_point_id].site_id
                if existing and existing.metering_point_id in metering_points_by_id
                else None
            )

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label(
                    "Zuordnung bearbeiten" if existing else "Neue Zuordnung"
                ).classes("text-lg font-bold")
                site_select = ui.select(
                    {None: "Alle Standorte", **site_options},
                    label="Standort (Filter für Messpunkt)",
                    value=initial_site_id,
                    with_input=True,
                ).classes("w-full")
                metering_point_select = ui.select(
                    metering_point_options_for(initial_site_id),
                    label="Messpunkt",
                    value=existing.metering_point_id if existing else None,
                    with_input=True,
                ).classes("w-full")
                leg_warning = ui.label("").classes("text-warning text-body2")

                def on_site_change() -> None:
                    """Re-filter the MeteringPoint options to the selected site.

                    Returns:
                        None.
                    """
                    options = metering_point_options_for(site_select.value)
                    metering_point_select.options = options
                    if metering_point_select.value not in options:
                        # Never guess a MeteringPoint from the newly filtered
                        # list -- clear the selection and let the
                        # administrator pick explicitly.
                        metering_point_select.value = None
                    metering_point_select.update()
                    update_leg_warning()

                site_select.on_value_change(lambda _: on_site_change())

                def update_leg_warning() -> None:
                    """Show a warning if the selected MeteringPoint's LEG mixes substation areas.

                    Lets the administrator immediately see, while assigning
                    a Person, whether the resulting LEG membership implies
                    a reduced BKW discount -- see `app.domain.leg_composition`.

                    Returns:
                        None.
                    """
                    mp = metering_points_by_id.get(metering_point_select.value)
                    if mp is None or mp.leg_id is None:
                        leg_warning.text = ""
                        return
                    with connection_scope() as connection:
                        composition = compute_leg_composition(connection, mp.leg_id)
                        leg = leg_repo.get(connection, mp.leg_id)
                    if composition.is_mixed and leg is not None:
                        substation_area_names = ", ".join(t.name for t in composition.substation_areas)
                        leg_warning.text = (
                            f"⚠ Die LEG „{leg.name}“ dieses Messpunkts umfasst "
                            f"mehrere Trafokreise ({substation_area_names}) -- "
                            "informieren Sie die Person ggf. über den "
                            "dadurch tieferen BKW-Rabatt."
                        )
                    else:
                        leg_warning.text = ""

                metering_point_select.on_value_change(lambda _: update_leg_warning())
                update_leg_warning()
                person_select = ui.select(
                    person_options,
                    label="Person",
                    value=existing.person_id if existing else None,
                ).classes("w-full")
                valid_from = ui.input(
                    "Gültig von",
                    value=existing.valid_from.isoformat() if existing else date.today().isoformat(),
                ).props("type=date").classes("w-full")
                valid_to = ui.input(
                    "Gültig bis (leer = offen)",
                    value=existing.valid_to.isoformat() if existing and existing.valid_to else "",
                ).props("type=date").classes("w-full")
                error_label = ui.label("").classes("text-negative")

                def save() -> None:
                    """Validate the form and persist the Assignment.

                    Returns:
                        None.
                    """
                    if metering_point_select.value is None or person_select.value is None:
                        error_label.text = "Messpunkt und Person sind erforderlich."
                        return
                    try:
                        from_date = _parse_date(valid_from.value)
                        to_date = _parse_date(valid_to.value)
                    except ValueError:
                        error_label.text = "Ungültiges Datum."
                        return
                    if from_date is None:
                        error_label.text = "„Gültig von“ ist erforderlich."
                        return
                    if to_date is not None and to_date < from_date:
                        error_label.text = "„Gültig bis“ darf nicht vor „Gültig von“ liegen."
                        return

                    with connection_scope() as connection:
                        if existing:
                            updated = Assignment(
                                id=existing.id,
                                person_id=person_select.value,
                                metering_point_id=metering_point_select.value,
                                valid_from=from_date,
                                valid_to=to_date,
                                created_at=existing.created_at,
                            )
                            assignment_repo.update(connection, updated)
                        else:
                            new_assignment = Assignment(
                                id=None,
                                person_id=person_select.value,
                                metering_point_id=metering_point_select.value,
                                valid_from=from_date,
                                valid_to=to_date,
                                created_at="",
                            )
                            assignment_repo.create(connection, new_assignment)
                    dialog.close()
                    # Notify before refresh() and via safe_notify(): the card
                    # whose button opened this dialog gets deleted by refresh()'s
                    # list_container rebuild, which can tear down this dialog's
                    # own UI context first -- see app.gui.safe_notify.
                    safe_notify("Gespeichert.", type="positive")
                    refresh()

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    ui.button("Speichern", on_click=save)
            dialog.open()

        def on_edit(assignment: Assignment) -> None:
            """Card edit-button handler: open the edit dialog for this Assignment.

            Args:
                assignment: Assignment to edit.

            Returns:
                None.
            """
            open_form(assignment)

        def on_remove(assignment: Assignment) -> None:
            """Card delete-button handler: delete the Assignment after confirmation.

            Args:
                assignment: Assignment to delete.

            Returns:
                None.
            """
            with ui.dialog() as confirm, ui.card():
                ui.label("Diese Zuordnung wirklich löschen?")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            assignment_repo.delete(connection, assignment.id)
                        confirm.close()
                        # notify before refresh() -- see save() above for why
                        safe_notify("Gelöscht.", type="warning")
                        refresh()

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        refresh()
