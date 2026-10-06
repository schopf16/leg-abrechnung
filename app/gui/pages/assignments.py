"""MeteringPoint-to-Person assignment history page (assignments)."""

from datetime import date, datetime
from typing import Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.filter_bar import FilterBar
from app.gui.form_dialog import form_guard
from app.gui.navigation import page_frame
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.gui.table_list import paged_table
from app.gui.sorting import (
    SortOption,
    address_key,
    apply_sort,
    fold_for_sort,
    sort_description,
    text_key,
)
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import assignment as assignment_repo
from app.models.assignment import Assignment


#: `(label, field)` pairs for the printed table -- one row per Assignment,
#: flattened out of the on-screen per-MeteringPoint card grouping.
#: What the list shows. One row per Zuordnung rather than a card per
#: Messpunkt: the grouping was presentation, and the default order still
#: keeps a Messpunkt's rows together, so the sequence that makes a move
#: legible is intact.
COLUMNS = [
    {"name": "metering_point", "label": "Messpunkt", "field": "metering_point", "align": "left"},
    {"name": "site", "label": "Standort", "field": "site", "align": "left"},
    {"name": "person_name", "label": "Person", "field": "person_name", "align": "left"},
    {"name": "valid_from", "label": "Gültig von", "field": "valid_from", "align": "left"},
    {"name": "valid_to", "label": "Gültig bis", "field": "valid_to", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]


PRINT_COLUMNS = [
    ("Messpunkt", "metering_point"),
    ("Person", "person_name"),
    ("Gültig von", "valid_from"),
    ("Gültig bis", "valid_to"),
]


def _descending(iso_date: str) -> tuple[int, ...]:
    """Key an ISO date so that the newest sorts first in an ascending sort."""
    return tuple(-int(part) for part in iso_date.split("-"))


#: Orders the Zuordnungen list offers, default first. These sort the
#: MeteringPoint *cards*; the assignments inside one card always stay in
#: chronological order, where the sequence itself is the information.
SORT_OPTIONS = [
    SortOption("metering_point", "Messpunkt", lambda g: text_key(g["designation"])),
    SortOption(
        "address",
        "Adresse",
        lambda g: (address_key(g["street"], g["house_number"]), text_key(g["designation"])),
    ),
    SortOption("person", "Person", lambda g: (text_key(*g["person_names"]), text_key(g["designation"]))),
    SortOption(
        "latest_valid_from",
        "Zuletzt begonnen",
        # Negated (as a reversed string comparison) rather than
        # `reverse=True`, which would also flip the name tiebreak.
        lambda g: (_descending(g["latest_valid_from"]), text_key(g["designation"])),
    ),
]


def _parse_date(value: str) -> Optional[date]:
    """Parse a date string from a NiceGUI date input into a `date`."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


@ui.page("/assignments")
def assignments_page() -> None:
    """Render the assignments CRUD page, including consistency warnings."""
    with page_frame("/assignments", "Zuordnungen"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Legt fest, welcher Person ein Messpunkt in welchem Zeitraum "
                "zugeordnet ist. Bei einem Umzug mitten im Quartal zwei "
                "Zuordnungen mit passendem Enddatum/Startdatum anlegen."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    heading="Zuordnungen",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: print_rows,
                    get_filter_description=lambda: _filter_description(),
                    # Named on its own line: a printout is read away from
                    # the screen, where the order is not self-evident.
                    get_sort_description=lambda: sort_description(SORT_OPTIONS, sort_select),
                )
                ui.button("+ Neue Zuordnung", on_click=lambda: open_form(None))

        bar = FilterBar("/assignments")
        search_input = bar.search("Messpunkt, Person, Adresse")
        sort_select = bar.sort(SORT_OPTIONS, lambda: refresh())
        only_current_switch = bar.filter("Nur laufende oder künftige Zuordnungen")

        search_input.on_value_change(lambda _: refresh())
        only_current_switch.on_value_change(lambda _: refresh())

        warnings_column = ui.column().classes("w-full")
        table = paged_table(route="/assignments", columns=COLUMNS, rows=[], row_key="id").classes(
            "w-full mt-2"
        )
        table.add_slot(
            "body-cell-actions",
            """
            <q-td :props="props">
                <q-btn dense flat icon="edit" @click="() => $parent.$emit('edit', props.row)" />
                <q-btn dense flat icon="delete" color="negative"
                       @click="() => $parent.$emit('remove', props.row)" />
            </q-td>
            """,
        )

        print_rows: list[dict] = []

        def _filter_description() -> str | None:
            """Build a short description of the currently active filter."""
            parts = []
            if search_input.value:
                parts.append(f'Suche: "{search_input.value.strip()}"')
            if only_current_switch.value:
                parts.append("nur laufende oder künftige Zuordnungen")
            return ", ".join(parts) if parts else None

        def refresh() -> None:
            """Reload the assignments list (grouped by MeteringPoint) and recompute consistency..."""
            nonlocal print_rows
            # One moment for the whole pass, so two cards cannot disagree
            # about what "laufend" means mid-render.
            now = datetime.now()
            with connection_scope() as connection:
                metering_points = {mp.id: mp for mp in metering_point_repo.list_all(connection)}
                sites = {s.id: s for s in site_repo.list_all(connection)}
                persons = {p.id: p for p in person_repo.list_all(connection)}
                assignments = assignment_repo.list_all(connection)
                all_warnings = []
                for metering_point_id in metering_points:
                    all_warnings.extend(assignment_repo.find_warnings(connection, metering_point_id))

            rows_by_metering_point: dict[int, list[dict]] = {}
            for z in assignments:
                rows_by_metering_point.setdefault(z.metering_point_id, []).append(
                    {
                        "assignment": z,
                        "person_name": persons[z.person_id].display_name if z.person_id in persons else "?",
                        # ISO for the sort keys below, German for reading:
                        # `latest_valid_from` compares these as text.
                        "valid_from": z.valid_from.isoformat(),
                        "valid_to": z.valid_to.isoformat() if z.valid_to else "offen",
                        "valid_from_display": z.valid_from.strftime("%d.%m.%Y"),
                        "valid_to_display": (z.valid_to.strftime("%d.%m.%Y") if z.valid_to else "offen"),
                    }
                )

            groups = []
            for metering_point_id, rows in rows_by_metering_point.items():
                mp = metering_points.get(metering_point_id)
                site = sites.get(mp.site_id) if mp else None
                if mp is None:
                    label = f"Messpunkt #{metering_point_id}"
                else:
                    label = f"{mp.designation} — {site.full_address if site else '?'}"
                groups.append(
                    {
                        "label": label,
                        "rows": rows,
                        "designation": mp.designation if mp else label,
                        "address": site.full_address if site else "?",
                        "street": site.street if site else "",
                        "house_number": site.house_number if site else "",
                        # Every person on this MeteringPoint, so sorting by
                        # person puts the card where its earliest name belongs
                        # and stays stable once a second person is added.
                        "person_names": sorted((row["person_name"] for row in rows), key=fold_for_sort),
                        "latest_valid_from": max(row["valid_from"] for row in rows),
                        # Whether this MeteringPoint has anybody on it now or
                        # soon. "Or soon" on purpose, and the label says so:
                        # pre-entering a whole quarter's move-ins ahead of
                        # time is the normal workflow here (see
                        # `app.domain.participant_mix`), and hiding those
                        # would bury exactly the ones needing attention.
                        # The card keeps showing its full history either
                        # way -- the sequence is the information; the switch
                        # only decides which cards are worth seeing.
                        "has_current_or_upcoming": any(
                            row["assignment"].is_current_or_upcoming(now) for row in rows
                        ),
                        "_search": " ".join([label] + [row["person_name"] for row in rows]).lower(),
                    }
                )

            needle = (search_input.value or "").strip().lower()
            visible = [
                g
                for g in groups
                if (not needle or needle in g["_search"])
                and (not only_current_switch.value or g["has_current_or_upcoming"])
            ]

            print_rows = []
            table_rows = []
            # Sorted as groups and then flattened, so the order is exactly
            # what the cards had: a Messpunkt's assignments stay adjacent and
            # in sequence, which is the part worth keeping about the
            # grouping. The printout is built from the same pass, so screen
            # and paper cannot disagree.
            for group in apply_sort(visible, SORT_OPTIONS, sort_select):
                for row in group["rows"]:
                    table_rows.append(
                        {
                            "id": row["assignment"].id,
                            "metering_point": group["designation"],
                            "site": group["address"],
                            "person_name": row["person_name"],
                            "valid_from": row["valid_from_display"],
                            "valid_to": row["valid_to_display"],
                        }
                    )
                    print_rows.append(
                        {
                            "metering_point": group["label"],
                            "person_name": row["person_name"],
                            "valid_from": row["valid_from_display"],
                            "valid_to": row["valid_to_display"],
                        }
                    )

            table.rows = table_rows
            table.update()

            warnings_column.clear()
            with warnings_column:
                for warning in all_warnings:
                    ui.label(f"⚠ {warning.message}").classes("text-negative text-body2")

        def open_form(existing: Optional[Assignment]) -> None:
            """Open the create/edit dialog for a Assignment."""
            with connection_scope() as connection:
                metering_points = metering_point_repo.list_all(connection)
                sites = site_repo.list_all(connection)
                persons = person_repo.list_all(connection)
            metering_points_by_id = {mp.id: mp for mp in metering_points}
            site_options = {s.id: s.full_address for s in sites}

            def metering_point_options_for(site_id: Optional[int]) -> dict:
                """Build the MeteringPoint dropdown options, optionally filtered by site."""
                return {
                    mp.id: f"{mp.designation} ({'Bezug' if mp.is_consumption else 'Einspeisung'})"
                    for mp in metering_points
                    if site_id is None or mp.site_id == site_id
                }

            # Deactivated persons are hidden from selection for new assignments,
            # but stay selectable when editing a Assignment that already points
            # at one (see app.models.person.delete).
            selectable_persons = [p for p in persons if p.active or (existing and p.id == existing.person_id)]
            person_options = {
                p.id: p.display_name + ("" if p.active else " (inaktiv)") for p in selectable_persons
            }

            initial_site_id = (
                metering_points_by_id[existing.metering_point_id].site_id
                if existing and existing.metering_point_id in metering_points_by_id
                else None
            )

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Zuordnung bearbeiten" if existing else "Neue Zuordnung").classes(
                    "text-lg font-bold"
                )
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

                def on_site_change() -> None:
                    """Re-filter the MeteringPoint options to the selected site."""
                    options = metering_point_options_for(site_select.value)
                    metering_point_select.options = options
                    if metering_point_select.value not in options:
                        # Never guess a MeteringPoint from the newly filtered
                        # list -- clear the selection and let the
                        # administrator pick explicitly.
                        metering_point_select.value = None
                    metering_point_select.update()

                site_select.on_value_change(lambda _: on_site_change())

                person_select = ui.select(
                    person_options,
                    label="Person",
                    value=existing.person_id if existing else None,
                ).classes("w-full")
                valid_from = (
                    ui.input(
                        "Gültig von",
                        value=existing.valid_from.isoformat() if existing else date.today().isoformat(),
                    )
                    .props("type=date")
                    .classes("w-full")
                )
                valid_to = (
                    ui.input(
                        "Gültig bis (leer = offen)",
                        value=existing.valid_to.isoformat() if existing and existing.valid_to else "",
                    )
                    .props("type=date")
                    .classes("w-full")
                )
                error_label = ui.label("").classes("text-negative")

                def save() -> None:
                    """Validate the form and persist the Assignment."""
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
            form_guard(dialog, on_save=save)
            dialog.open()

        def on_edit(assignment: Assignment) -> None:
            """Card edit-button handler: open the edit dialog for this Assignment."""
            open_form(assignment)

        def on_remove(assignment: Assignment) -> None:
            """Card delete-button handler: delete the Assignment after confirmation."""
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

        def _with_assignment(action):
            """Wrap a handler so it receives the Zuordnung, not the row."""

            def handle(event) -> None:
                """Resolve the clicked row and run the action."""
                with connection_scope() as connection:
                    existing = assignment_repo.get(connection, event.args["id"])
                if existing is None:
                    safe_notify("Diese Zuordnung gibt es nicht mehr.", type="warning")
                    refresh()
                    return
                action(existing)

            return handle

        table.on("edit", _with_assignment(on_edit))
        table.on("remove", _with_assignment(on_remove))

        refresh()
