"""Austritte page: tracks each person's progress through the four
real-world steps of a LEG membership ending (see `app.models.
person_offboarding`) -- the reverse of `app.gui.pages.onboardings`.

A tracker only exists once explicitly started -- manually here (for a
normal voluntary exit), or via the "Ausschluss-Prozess starten" link
Michael follows from the dunning page once a 2. dunning notice goes unpaid
(never automatically). Deleting a tracker only discards the tracking
record; it never touches the Person or their receivables claim.
"""

from datetime import date, datetime

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.gui.offboarding_form import open_offboarding_form
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.models import person as person_repo
from app.models import person_offboarding as person_offboarding_repo
from app.models.person import Person
from app.models.person_offboarding import REASON_OPTIONS, STEPS, PersonOffboarding

#: `(label, field)` pairs for the printed table: Person/reason/status, then
#: one column per offboarding step (its date, or empty if still open).
PRINT_COLUMNS = [("Person", "person"), ("Grund", "reason"), ("Status", "status")] + [
    (label, attr) for attr, label in STEPS
]


def _print_row(offboarding: PersonOffboarding, person: Person) -> dict:
    """Convert one offboarding tracker into a row dict for the printed table.

    Args:
        offboarding: Tracker to convert.
        person: The tracked person.

    Returns:
        A dict with the fields required by `PRINT_COLUMNS`.
    """
    if offboarding.is_complete:
        status = "Abgeschlossen"
    else:
        _, step_label = offboarding.current_step
        status = f"{step_label} (seit {offboarding.days_open()} Tagen)"
    row = {
        "person": person.display_name,
        "reason": REASON_OPTIONS.get(offboarding.reason, offboarding.reason),
        "status": status,
    }
    for attr, _ in STEPS:
        value = getattr(offboarding, attr)
        row[attr] = value.isoformat() if value else ""
    return row


def _parse_date(value: str) -> date:
    """Parse a date string from a NiceGUI date input into a `date`.

    Args:
        value: Date string in ISO format ("YYYY-MM-DD").

    Returns:
        The parsed `date`.
    """
    return datetime.strptime(value, "%Y-%m-%d").date()


@ui.page("/offboardings")
def offboardings_page() -> None:
    """Render the Austritte (offboarding tracking) page.

    Returns:
        None.
    """
    with page_frame("/offboardings", "Austritte"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Fortschritt durch die vier Schritte eines LEG-Austritts: "
                "Austritt/Ausschluss beschlossen, Austrittsdatum Messpunkt "
                "festgelegt, BKW informiert, Person schriftlich bestätigt. "
                "Ein Ausschluss beendet die Mitgliedschaft, nie die offene "
                "Forderung -- die bleibt unverändert bestehen (siehe "
                "„Debitoren“)."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    heading="Austritte",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: [
                        _print_row(o, persons[o.person_id])
                        for o in visible_offboardings
                        if o.person_id in persons
                    ],
                    get_filter_description=lambda: _filter_description(),
                )
                ui.button("+ Austritt starten", on_click=lambda: on_start())

        show_complete_switch = ui.switch("Auch abgeschlossene anzeigen")
        list_container = ui.column().classes("w-full gap-2 mt-2")

        visible_offboardings: list[PersonOffboarding] = []
        persons: dict[int, Person] = {}

        def _filter_description() -> str | None:
            """Build a short description of the currently active filter.

            Returns:
                A human-readable summary, or `None` if no filter is active.
            """
            return "inkl. abgeschlossene" if show_complete_switch.value else None

        def render_card(offboarding: PersonOffboarding, person: Person) -> None:
            """Render one offboarding tracker as a card.

            Args:
                offboarding: Tracker to render.
                person: The tracked person.

            Returns:
                None.
            """
            with ui.card().classes("w-full" + (" opacity-60" if offboarding.is_complete else "")):
                with ui.row().classes("w-full items-start gap-6 flex-wrap"):
                    with ui.column().classes("gap-0 min-w-[220px]"):
                        with ui.row().classes("items-center gap-2"):
                            ui.link(person.display_name, f"/persons/{person.id}").classes("font-bold")
                            ui.badge(REASON_OPTIONS.get(offboarding.reason, offboarding.reason))
                            if offboarding.is_complete:
                                ui.badge("Abgeschlossen", color="positive")
                        if not offboarding.is_complete:
                            _, step_label = offboarding.current_step
                            ui.label(f"Aktueller Schritt: {step_label}").classes(
                                "text-caption text-grey-6"
                            )
                    with ui.column().classes("gap-0 min-w-[280px]"):
                        for attr, label in STEPS:
                            value = getattr(offboarding, attr)
                            text = f"{'✓' if value else '—'} {label}"
                            if value:
                                text += f" ({value.isoformat()})"
                            ui.label(text).classes(
                                "text-caption" + ("" if value else " text-grey-6")
                            )
                    with ui.row().classes("gap-1 ml-auto"):
                        ui.button(
                            "Bearbeiten", on_click=lambda o=offboarding, p=person: on_edit(o, p)
                        ).props("dense flat")
                        ui.button(
                            "Löschen", on_click=lambda o=offboarding, p=person: on_delete(o, p)
                        ).props("dense flat color=negative")

        def refresh() -> None:
            """Reload the offboarding list according to the current filter.

            Returns:
                None.
            """
            nonlocal visible_offboardings, persons
            with connection_scope() as connection:
                offboardings = (
                    person_offboarding_repo.list_all(connection)
                    if show_complete_switch.value
                    else person_offboarding_repo.list_in_progress(connection)
                )
                persons = {p.id: p for p in person_repo.list_all(connection)}
            visible_offboardings = offboardings
            list_container.clear()
            with list_container:
                if not offboardings:
                    ui.label("Keine passenden Austritte.")
                for offboarding in offboardings:
                    person = persons.get(offboarding.person_id)
                    if person is None:
                        continue
                    render_card(offboarding, person)

        show_complete_switch.on_value_change(lambda _: refresh())

        def on_edit(offboarding: PersonOffboarding, person: Person) -> None:
            """Card button handler: open the edit dialog for this tracker.

            Args:
                offboarding: Tracker to edit.
                person: The tracked person.

            Returns:
                None.
            """
            open_offboarding_form(offboarding, person, on_saved=lambda _: refresh())

        def on_delete(offboarding: PersonOffboarding, person: Person) -> None:
            """Card button handler: discard a tracker after confirmation.

            Args:
                offboarding: Tracker to delete.
                person: The tracked person (display only -- never deleted).

            Returns:
                None.
            """
            with ui.dialog() as confirm, ui.card():
                ui.label(f'Austrittsprozess von "{person.display_name}" wirklich löschen?')
                ui.label(
                    "Nur die Nachverfolgung wird entfernt -- die Person, "
                    "ihre Zuordnungen und ihr Saldo bleiben unverändert."
                ).classes("text-caption text-grey-7")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            person_offboarding_repo.delete(connection, offboarding.id)
                        confirm.close()
                        safe_notify("Gelöscht.", type="warning")
                        refresh()

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        def on_start() -> None:
            """Top button handler: start a voluntary offboarding for an
            existing Person.

            Returns:
                None.
            """
            with connection_scope() as connection:
                all_persons = person_repo.list_all(connection)
                already_tracked = {o.person_id for o in person_offboarding_repo.list_all(connection)}
            available_persons = [p for p in all_persons if p.id not in already_tracked]
            if not available_persons:
                ui.notify("Für alle Personen läuft bereits ein Austrittsprozess.", type="warning")
                return
            person_options = {p.id: p.display_name for p in available_persons}

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Austritt starten").classes("text-lg font-bold")
                person_select = ui.select(
                    person_options, label="Person", with_input=True
                ).classes("w-full")
                start_date = ui.input(
                    "Austritt/Ausschluss beschlossen (Datum)", value=date.today().isoformat()
                ).props("type=date").classes("w-full")
                error_label = ui.label("").classes("text-negative")

                def start() -> None:
                    """Validate the form and start the offboarding tracker.

                    Returns:
                        None.
                    """
                    if person_select.value is None:
                        error_label.text = "Bitte eine Person wählen."
                        return
                    try:
                        decided_at = _parse_date(start_date.value)
                    except ValueError:
                        error_label.text = "Ungültiges Datum."
                        return
                    with connection_scope() as connection:
                        person_offboarding_repo.start_for_person(
                            connection, person_select.value, reason="freiwillig", decided_at=decided_at
                        )
                    dialog.close()
                    safe_notify("Austritt gestartet.", type="positive")
                    refresh()

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    ui.button("Starten", on_click=start)
            dialog.open()

        refresh()
