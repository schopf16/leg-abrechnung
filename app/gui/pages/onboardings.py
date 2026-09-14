"""Aufnahmen page: tracks each interested person's progress through the
five real-world onboarding steps (see `app.models.person_onboarding`).

A tracker only exists for a person once explicitly started -- either
automatically when a Web-Registrierung is taken over ("Person übernehmen",
see `app.gui.pages.web_registrations`), or manually here (e.g. for
someone who inquired by phone rather than through the web form). Deleting
a tracker only discards the tracking record; it never touches the Person.
"""

import unicodedata
from datetime import date, datetime

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.gui.onboarding_form import open_onboarding_form
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.models import person as person_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models import settings as settings_repo
from app.models.person import Person
from app.models.person_onboarding import STEPS, PersonOnboarding

#: Options for the "Schritt-Filter" select: which single step must still be
#: open (its date field `None`) for a tracker to match. Phrased to match
#: how Michael actually describes them ("wer hat den Vertrag nicht
#: unterschrieben") rather than reusing the neutral `STEPS` labels as-is.
#: `None` means "no filter, show everything".
STEP_FILTER_OPTIONS: dict[str | None, str] = {
    None: "Alle Schritte",
    "registered_at": "Anmeldung bei uns steht noch aus",
    "leg_assigned_at": "Einteilung in LEG steht noch aus",
    "contract_signed_at": "Vertrag noch nicht unterschrieben",
    "bkw_registered_at": "Noch nicht bei der BKW angemeldet",
    "bkw_confirmed_at": "Noch nicht von der BKW bestätigt",
}


#: Options for the "Sortierung" select. Surname first by default: the
#: administrator looks people up by name, while the database's own order
#: (when tracking was started) is only meaningful for the two date-based
#: options below.
SORT_OPTIONS: dict[str, str] = {
    "last_name": "Nachname",
    "registered_at": "Anmeldedatum",
    "current_step": "Aktueller Schritt",
    "days_open": "Offen seit (längste zuerst)",
}

#: Default sort, see `SORT_OPTIONS`.
DEFAULT_SORT = "last_name"


#: `(label, field)` pairs for the printed table: Person/Status, then one
#: column per onboarding step (its date, or empty if still open).
PRINT_COLUMNS = [("Person", "person"), ("Status", "status")] + [(label, attr) for attr, label in STEPS]


def _sortable(text: str) -> str:
    """Fold a name to something that sorts the way a German reader expects.

    Umlauts sort as their base letter (DIN 5007 Variant 1: "Bühler" before
    "Burri", "Müller" before "Muzzolini"), and accents on French-Swiss
    names are folded the same way. Plain code-point ordering would put
    every umlaut after "z" instead, which is what the raw SQL ordering
    still does elsewhere in the app.

    Args:
        text: Raw name part.

    Returns:
        A lowercased, accent-free version for comparison only -- never
        shown to anyone.
    """
    decomposed = unicodedata.normalize("NFD", text.strip().replace("ß", "ss"))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _person_name_key(person: Person) -> tuple[str, str]:
    """Build the surname-first sort key for a person.

    Follows `person_repo.list_all`'s rule (surname, falling back to the
    company name for a company without a contact person, then first name),
    but folds umlauts (see `_sortable`) so the list reads correctly.

    Args:
        person: Person to build the key for.

    Returns:
        `(primary, first_name)`, both folded for comparison.
    """
    primary = person.last_name.strip() or person.company.strip()
    return (_sortable(primary), _sortable(person.first_name))


def sort_onboardings(
    onboardings: list[PersonOnboarding], persons: dict[int, Person], sort_by: str
) -> list[PersonOnboarding]:
    """Sort onboarding trackers according to one of `SORT_OPTIONS`.

    Every ordering falls back to the person's name for ties, so the list
    never reshuffles arbitrarily between two refreshes. Finished trackers
    sort last for the two "how far along / how stuck is this" orderings,
    where they carry no useful value.

    Args:
        onboardings: Trackers to sort.
        persons: `{person_id: Person}` lookup for the tracked persons.
        sort_by: One of `SORT_OPTIONS`' keys; an unknown value sorts by
            name, the default.

    Returns:
        A new, sorted list; the input is left untouched.
    """
    step_attributes = [attr for attr, _ in STEPS]

    def key(onboarding: PersonOnboarding):
        person = persons.get(onboarding.person_id)
        name = _person_name_key(person) if person else ("", "")

        if sort_by == "registered_at":
            # Trackers started but not yet dated fall back to the day the
            # tracking row was created, so they stay in the same ballpark
            # instead of all bunching up at the very top.
            registered = onboarding.registered_at or date.fromisoformat(onboarding.created_at[:10])
            return (registered.isoformat(), name)

        if sort_by == "current_step":
            position = (
                len(step_attributes)
                if onboarding.is_complete
                else step_attributes.index(onboarding.current_step[0])
            )
            return (position, name)

        if sort_by == "days_open":
            days_open = onboarding.days_open()
            # Negated so the longest-open tracker comes first; completed
            # ones have no value at all and go to the end.
            return (1, 0, name) if days_open is None else (0, -days_open, name)

        return name

    return sorted(onboardings, key=key)


def _print_row(onboarding: PersonOnboarding, person: Person, threshold_days: int) -> dict:
    """Convert one onboarding tracker into a row dict for the printed table.

    Args:
        onboarding: Tracker to convert.
        person: The tracked person.
        threshold_days: Current `onboarding_overdue_days` setting, to
            flag an overdue step on the printout too.

    Returns:
        A dict with the fields required by `PRINT_COLUMNS`.
    """
    if onboarding.is_complete:
        status = "Abgeschlossen"
    else:
        _, step_label = onboarding.current_step
        status = f"{step_label} (seit {onboarding.days_open()} Tagen)"
        if onboarding.is_overdue(threshold_days):
            status += " -- überfällig"
    row = {"person": person.display_name, "status": status}
    for attr, _ in STEPS:
        value = getattr(onboarding, attr)
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


@ui.page("/onboardings")
def onboardings_page() -> None:
    """Render the Aufnahmen (onboarding tracking) page.

    Returns:
        None.
    """
    with page_frame("/onboardings", "Aufnahmen"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Fortschritt interessierter Personen durch die fünf Schritte "
                "bis zur vollständigen LEG-Mitgliedschaft: Anmeldung bei uns, "
                "Einteilung in LEG, Gesellschaftsvertrag, Anmeldung bei der "
                "BKW, Bestätigung durch die BKW. Wird automatisch gestartet, "
                "wenn eine Web-Registrierung übernommen wird -- oder hier "
                "manuell für eine bestehende Person."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    heading="Aufnahmen",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: [
                        _print_row(o, persons[o.person_id], threshold_days)
                        for o in visible_onboardings
                        if o.person_id in persons
                    ],
                    get_filter_description=lambda: _filter_description(),
                )
                ui.button("+ Aufnahme starten", on_click=lambda: on_start())

        with ui.row().classes("w-full items-center gap-4"):
            show_complete_switch = ui.switch("Auch abgeschlossene anzeigen")
            step_filter = ui.select(STEP_FILTER_OPTIONS, value=None, label="Schritt-Filter").classes(
                "w-full max-w-sm"
            )
            sort_select = ui.select(SORT_OPTIONS, value=DEFAULT_SORT, label="Sortierung").classes(
                "w-full max-w-xs"
            )
        list_container = ui.column().classes("w-full gap-2 mt-2")

        visible_onboardings: list[PersonOnboarding] = []
        persons: dict[int, Person] = {}
        threshold_days = 30

        def _filter_description() -> str | None:
            """Build a short description of the currently active filters.

            Returns:
                A human-readable summary, or `None` if no filter is active.
            """
            parts = []
            if show_complete_switch.value:
                parts.append("inkl. abgeschlossene")
            if step_filter.value is not None:
                parts.append(STEP_FILTER_OPTIONS[step_filter.value])
            # Always named: the printout is read away from the screen, where
            # the order is not self-evident.
            parts.append(f"sortiert nach {SORT_OPTIONS[sort_select.value or DEFAULT_SORT]}")
            return ", ".join(parts) if parts else None

        def render_card(onboarding: PersonOnboarding, person: Person, threshold_days: int) -> None:
            """Render one onboarding tracker as a card.

            Args:
                onboarding: Tracker to render.
                person: The tracked person.
                threshold_days: Current `onboarding_overdue_days` setting.

            Returns:
                None.
            """
            overdue = onboarding.is_overdue(threshold_days)
            with ui.card().classes("w-full" + (" opacity-60" if onboarding.is_complete else "")):
                with ui.row().classes("w-full items-start gap-6 flex-wrap"):
                    with ui.column().classes("gap-0 min-w-[220px]"):
                        with ui.row().classes("items-center gap-2"):
                            ui.link(person.display_name, f"/persons/{person.id}").classes("font-bold")
                            if onboarding.is_complete:
                                ui.badge("Abgeschlossen", color="positive")
                            elif overdue:
                                ui.badge(f"{onboarding.days_open()} Tage überfällig", color="negative")
                        if not onboarding.is_complete:
                            _, step_label = onboarding.current_step
                            ui.label(f"Aktueller Schritt: {step_label}").classes("text-caption text-grey-6")
                    with ui.column().classes("gap-0 min-w-[280px]"):
                        for attr, label in STEPS:
                            value = getattr(onboarding, attr)
                            text = f"{'✓' if value else '—'} {label}"
                            if value:
                                text += f" ({value.isoformat()})"
                            ui.label(text).classes("text-caption" + ("" if value else " text-grey-6"))
                    with ui.row().classes("gap-1 ml-auto"):
                        ui.button("Bearbeiten", on_click=lambda o=onboarding, p=person: on_edit(o, p)).props(
                            "dense flat"
                        )
                        ui.button("Löschen", on_click=lambda o=onboarding, p=person: on_delete(o, p)).props(
                            "dense flat color=negative"
                        )

        def refresh() -> None:
            """Reload the onboarding list according to the current filters.

            Returns:
                None.
            """
            nonlocal visible_onboardings, persons, threshold_days
            with connection_scope() as connection:
                onboardings = (
                    person_onboarding_repo.list_all(connection)
                    if show_complete_switch.value
                    else person_onboarding_repo.list_in_progress(connection)
                )
                persons = {p.id: p for p in person_repo.list_all(connection)}
                threshold_days = settings_repo.get_settings(connection).onboarding_overdue_days
            step_attr = step_filter.value
            if step_attr is not None:
                # A tracker matches only while that one step's own date is
                # still unset -- independent of `current_step`, since step
                # order isn't enforced (someone might sign the contract
                # before being assigned to a LEG).
                onboardings = [o for o in onboardings if getattr(o, step_attr) is None]
            onboardings = sort_onboardings(onboardings, persons, sort_select.value or DEFAULT_SORT)
            visible_onboardings = onboardings
            list_container.clear()
            with list_container:
                if not onboardings:
                    ui.label("Keine passenden Aufnahmen.")
                for onboarding in onboardings:
                    person = persons.get(onboarding.person_id)
                    if person is None:
                        continue
                    render_card(onboarding, person, threshold_days)

        show_complete_switch.on_value_change(lambda _: refresh())
        step_filter.on_value_change(lambda _: refresh())
        sort_select.on_value_change(lambda _: refresh())

        def on_edit(onboarding: PersonOnboarding, person: Person) -> None:
            """Card button handler: open the edit dialog for this tracker.

            Args:
                onboarding: Tracker to edit.
                person: The tracked person.

            Returns:
                None.
            """
            open_onboarding_form(onboarding, person, on_saved=lambda _: refresh())

        def on_delete(onboarding: PersonOnboarding, person: Person) -> None:
            """Card button handler: discard a tracker after confirmation.

            Args:
                onboarding: Tracker to delete.
                person: The tracked person (display only -- never deleted).

            Returns:
                None.
            """
            with ui.dialog() as confirm, ui.card():
                ui.label(f'Aufnahmeprozess von "{person.display_name}" wirklich löschen?')
                ui.label(
                    "Nur die Nachverfolgung wird entfernt -- die Person selbst bleibt bestehen."
                ).classes("text-caption text-grey-7")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            person_onboarding_repo.delete(connection, onboarding.id)
                        confirm.close()
                        # notify before refresh() -- see app.gui.safe_notify for why
                        safe_notify("Gelöscht.", type="warning")
                        refresh()

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        def on_start() -> None:
            """Top button handler: start onboarding tracking for an existing Person.

            Returns:
                None.
            """
            with connection_scope() as connection:
                all_persons = person_repo.list_all(connection)
                already_tracked = {o.person_id for o in person_onboarding_repo.list_all(connection)}
            available_persons = [p for p in all_persons if p.id not in already_tracked]
            if not available_persons:
                ui.notify("Alle Personen werden bereits nachverfolgt.", type="warning")
                return
            person_options = {p.id: p.display_name for p in available_persons}

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Aufnahme starten").classes("text-lg font-bold")
                person_select = ui.select(person_options, label="Person", with_input=True).classes("w-full")
                start_date = (
                    ui.input("Anmeldung bei uns (Datum)", value=date.today().isoformat())
                    .props("type=date")
                    .classes("w-full")
                )
                error_label = ui.label("").classes("text-negative")

                def start() -> None:
                    """Validate the form and start the onboarding tracker.

                    Returns:
                        None.
                    """
                    if person_select.value is None:
                        error_label.text = "Bitte eine Person wählen."
                        return
                    try:
                        registered_at = _parse_date(start_date.value)
                    except ValueError:
                        error_label.text = "Ungültiges Datum."
                        return
                    with connection_scope() as connection:
                        person_onboarding_repo.start_for_person(
                            connection, person_select.value, registered_at=registered_at
                        )
                    dialog.close()
                    safe_notify("Aufnahme gestartet.", type="positive")
                    refresh()

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    ui.button("Starten", on_click=start)
            dialog.open()

        refresh()
