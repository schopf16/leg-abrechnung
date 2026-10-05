"""Austritte page: tracks each person's progress through the four real-world steps of a LEG membership
ending (see `app.models. person_offboarding`) -- the reverse of `app.gui.pages.onboardings`."""

from datetime import date, datetime

from nicegui import ui

from app.db.connection import connection_scope
from app.formatting import format_date
from app.domain.global_search import person_matches
from app.domain.message_templates import DueMessage, due_by_person
from app.gui.filter_bar import FilterBar
from app.gui.form_dialog import form_guard
from app.gui.list_footer import render_count, render_empty
from app.gui.message_buttons import group_by_step, render_step_messages
from app.gui.navigation import page_frame
from app.gui.offboarding_form import open_offboarding_form, open_remove_person_dialog
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.gui.sorting import (
    SortOption,
    apply_sort,
    person_name_key,
    sort_description,
    text_key,
)
from app.models import person as person_repo
from app.models import person_offboarding as person_offboarding_repo
from app.models.message_template import OCCASION_OFFBOARDING
from app.models.person import Person
from app.models.person_offboarding import REASON_OPTIONS, STEPS, PersonOffboarding

#: `(label, field)` pairs for the printed table: Person/reason/status, then
#: one column per offboarding step (its date, or empty if still open).
PRINT_COLUMNS = [("Person", "person"), ("Grund", "reason"), ("Status", "status")] + [
    (label, attr) for attr, label in STEPS
]

#: Default order for the "Sortierung" select, see `sort_options`.
DEFAULT_SORT = "last_name"


def sort_options(persons: dict[int, Person]) -> list[SortOption]:
    """Build the orders the Austritte list offers."""
    step_attributes = [attr for attr, _ in STEPS]

    def name(offboarding: PersonOffboarding):
        return person_name_key(persons.get(offboarding.person_id))

    def decided(offboarding: PersonOffboarding):
        # A tracker started but not yet dated falls back to the day
        # tracking began, so it stays in the same ballpark instead of
        # bunching up at the very top.
        day = offboarding.decided_at or date.fromisoformat(offboarding.created_at[:10])
        return (day.isoformat(), name(offboarding))

    def current_step(offboarding: PersonOffboarding):
        position = (
            len(step_attributes)
            if offboarding.is_complete
            else step_attributes.index(offboarding.current_step[0])
        )
        return (position, name(offboarding))

    def reason(offboarding: PersonOffboarding):
        return (text_key(REASON_OPTIONS.get(offboarding.reason, offboarding.reason)), name(offboarding))

    return [
        SortOption(DEFAULT_SORT, "Nachname", name),
        SortOption("decided_at", "Beschlussdatum", decided),
        SortOption("current_step", "Aktueller Schritt", current_step),
        SortOption("reason", "Grund", reason),
    ]


def _print_row(offboarding: PersonOffboarding, person: Person) -> dict:
    """Convert one offboarding tracker into a row dict for the printed table."""
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
    """Parse a date string from a NiceGUI date input into a `date`."""
    return datetime.strptime(value, "%Y-%m-%d").date()


@ui.page("/offboardings")
def offboardings_page() -> None:
    """Render the Austritte (offboarding tracking) page."""
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
                    # Named on its own line: a printout is read away from
                    # the screen, where the order is not self-evident.
                    get_sort_description=lambda: sort_description(sort_options({}), sort_select),
                )
                ui.button("+ Austritt starten", on_click=lambda: on_start())

        bar = FilterBar("/offboardings")
        search_input = bar.search("Name, Firma, Kunden-Nr.")
        sort_select = bar.sort(sort_options({}), lambda: refresh())
        show_complete_switch = bar.filter("Auch abgeschlossene anzeigen")

        list_container = ui.column().classes("w-full gap-2 mt-2")

        visible_offboardings: list[PersonOffboarding] = []
        persons: dict[int, Person] = {}
        due_messages: dict[int, list[DueMessage]] = {}

        def _filter_description() -> str | None:
            """Build a short description of the currently active filter."""
            parts = []
            if search_input.value:
                parts.append(f'Suche "{search_input.value}"')
            if show_complete_switch.value:
                parts.append("inkl. abgeschlossene")
            else:
                needs_removal = sum(
                    1
                    for o in visible_offboardings
                    if o.is_complete and (p := persons.get(o.person_id)) is not None and p.active
                )
                if needs_removal:
                    parts.append(f"offene Austritte, inkl. {needs_removal} mit noch aktiver Person")
            return ", ".join(parts) if parts else None

        def render_card(offboarding: PersonOffboarding, person: Person) -> None:
            """Render one offboarding tracker as a card."""
            with ui.card().classes("w-full" + (" opacity-60" if offboarding.is_complete else "")):
                with ui.row().classes("w-full items-start gap-6 flex-wrap"):
                    with ui.column().classes("gap-0 min-w-[220px]"):
                        with ui.row().classes("items-center gap-2"):
                            ui.link(person.display_name, f"/persons/{person.id}").classes("font-bold")
                            ui.badge(REASON_OPTIONS.get(offboarding.reason, offboarding.reason))
                            if offboarding.is_complete and person.active:
                                # Not green: the steps are done but the
                                # consequence is not, and that is exactly
                                # the state that used to go unnoticed.
                                ui.badge("Person noch aktiv", color="orange")
                            elif offboarding.is_complete:
                                ui.badge("Abgeschlossen", color="positive")
                        if not offboarding.is_complete:
                            _, step_label = offboarding.current_step
                            ui.label(f"Aktueller Schritt: {step_label}").classes("text-caption text-grey-6")
                    # The mails sit on the row of the step they belong to --
                    # see the same comment in `onboardings.py`.
                    by_step = group_by_step(due_messages.get(offboarding.person_id, []))
                    with ui.column().classes("gap-0 grow min-w-[280px]"):
                        for attr, label in STEPS:
                            value = getattr(offboarding, attr)
                            text = f"{'✓' if value else '—'} {label}"
                            if value:
                                text += f" ({format_date(value)})"
                            with ui.row().classes("w-full items-center gap-3"):
                                ui.label(text).classes(
                                    "text-caption w-[260px] shrink-0" + ("" if value else " text-grey-6")
                                )
                                render_step_messages(
                                    person,
                                    by_step.get(attr, []),
                                    OCCASION_OFFBOARDING,
                                    on_changed=refresh,
                                )
                    with ui.row().classes("gap-1 ml-auto"):
                        if offboarding.is_complete and person.active:
                            ui.button(
                                "Person entfernen",
                                on_click=lambda p=person: open_remove_person_dialog(p, on_done=refresh),
                            ).props("dense outline color=negative")
                        ui.button("Bearbeiten", on_click=lambda o=offboarding, p=person: on_edit(o, p)).props(
                            "dense flat"
                        )
                        # "Austritt verwerfen", not "Löschen": the card can
                        # also carry "Person entfernen", and two red buttons
                        # reading alike put the only distinction in the
                        # confirmation text, where nobody looks first. The
                        # label now names what disappears.
                        ui.button(
                            "Austritt verwerfen", on_click=lambda o=offboarding, p=person: on_delete(o, p)
                        ).props("dense flat color=negative")

        def refresh() -> None:
            """Reload the offboarding list according to the current filter."""
            nonlocal visible_offboardings, persons, due_messages
            with connection_scope() as connection:
                all_offboardings = person_offboarding_repo.list_all(connection)
                persons = {p.id: p for p in person_repo.list_all(connection)}
                due_messages = due_by_person(connection, all_offboardings, OCCASION_OFFBOARDING)
                if show_complete_switch.value:
                    offboardings = all_offboardings
                else:
                    # A finished process whose person is still active is not
                    # finished business: hiding it is how one stayed active
                    # for weeks with nothing on any list saying so. Kept out
                    # of `person_offboarding.list_in_progress` on purpose --
                    # the Debitoren page reads that as "Austritt läuft",
                    # which this is not.
                    offboardings = [
                        o
                        for o in all_offboardings
                        if not o.is_complete
                        or (persons.get(o.person_id) is not None and persons[o.person_id].active)
                    ]
            query = search_input.value or ""
            if query.strip():
                offboardings = [
                    tracker
                    for tracker in offboardings
                    if (candidate := persons.get(tracker.person_id)) is not None
                    and person_matches(candidate, query)
                ]
            visible_offboardings = apply_sort(offboardings, sort_options(persons), sort_select)
            list_container.clear()
            with list_container:
                if not visible_offboardings:
                    render_empty(
                        "Keine passenden Austritte."
                        if bar.is_filtering()
                        else "Noch kein Austritt gestartet.",
                        action_label="Filter zurücksetzen" if bar.is_filtering() else None,
                        on_action=(lambda: bar.reset(refresh)) if bar.is_filtering() else None,
                    )
                else:
                    render_count(
                        visible=len(visible_offboardings),
                        total=len(all_offboardings),
                        noun="Austritte",
                    )
                for offboarding in visible_offboardings:
                    person = persons.get(offboarding.person_id)
                    if person is None:
                        continue
                    render_card(offboarding, person)

        search_input.on_value_change(lambda _: refresh())
        show_complete_switch.on_value_change(lambda _: refresh())

        def on_edit(offboarding: PersonOffboarding, person: Person) -> None:
            """Card button handler: open the edit dialog for this tracker."""
            open_offboarding_form(offboarding, person, on_saved=lambda _: refresh())

        def on_delete(offboarding: PersonOffboarding, person: Person) -> None:
            """Card button handler: discard a tracker after confirmation."""
            with ui.dialog() as confirm, ui.card():
                ui.label(f'Austritt von "{person.display_name}" wirklich verwerfen?')
                ui.label(
                    "Nur die Nachverfolgung verschwindet -- die Person, ihre "
                    "Zuordnungen und ihr Saldo bleiben unverändert. Zum "
                    "Entfernen der Person selbst dient „Person entfernen“."
                ).classes("text-caption text-grey-7")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            person_offboarding_repo.delete(connection, offboarding.id)
                        confirm.close()
                        safe_notify("Gelöscht.", type="warning")
                        refresh()

                    ui.button("Verwerfen", on_click=do_delete, color="negative")
            confirm.open()

        def on_start() -> None:
            """Top button handler: start a voluntary offboarding for an existing Person."""
            with connection_scope() as connection:
                all_persons = person_repo.list_all(connection)
                already_tracked = {o.person_id for o in person_offboarding_repo.list_all(connection)}
            # Active only: a deactivated person has left, and starting a
            # process for them is never what is meant. Somebody who really
            # comes back is reactivated first.
            available_persons = [p for p in all_persons if p.id not in already_tracked and p.active]
            if not available_persons:
                ui.notify("Für alle Personen läuft bereits ein Austrittsprozess.", type="warning")
                return
            person_options = {p.id: p.display_name for p in available_persons}

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Austritt starten").classes("text-lg font-bold")
                person_select = ui.select(person_options, label="Person", with_input=True).classes("w-full")
                start_date = (
                    ui.input("Austritt/Ausschluss beschlossen (Datum)", value=date.today().isoformat())
                    .props("type=date")
                    .classes("w-full")
                )
                error_label = ui.label("").classes("text-negative")

                def start() -> None:
                    """Validate the form and start the offboarding tracker."""
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
                            connection, person_select.value, reason="voluntary", decided_at=decided_at
                        )
                    dialog.close()
                    safe_notify("Austritt gestartet.", type="positive")
                    refresh()

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    ui.button("Starten", on_click=start)
            form_guard(dialog, on_save=start)
            dialog.open()

        refresh()
