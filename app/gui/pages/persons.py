"""persons management page: list, search, create, edit, delete, and a
detail drill-down showing the Person → Assignment → MeteringPoint (→ LEG,
→ site → substation area) join (project prompt section 7,
"persons-Detailansicht").

The list is rendered as one card per Person (not a single-row-per-person
table): a Person has enough fields (Name/Firma, Kontakt, billing address,
IBAN) that a flat table forces horizontal scrolling. Cards let each group
of fields wrap onto its own line instead.
"""

from datetime import date

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.iban_validation import format_iban
from app.domain.leg_composition import compute_leg_composition
from app.gui.navigation import page_frame
from app.gui.offboarding_form import open_offboarding_form
from app.gui.onboarding_form import open_onboarding_form
from app.gui.person_form import open_person_form
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import person_offboarding as person_offboarding_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models import assignment as assignment_repo
from app.models.person_offboarding import REASON_OPTIONS
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN
from app.models.person import Person

DIRECTION_LABELS = {
    DIRECTION_CONSUMPTION: "Bezug",
    DIRECTION_FEED_IN: "Einspeisung",
}


def _copy_customer_number(person: Person) -> None:
    """Copy a person's formatted customer number to the clipboard and confirm.

    Args:
        person: Person whose customer number to copy.

    Returns:
        None.
    """
    ui.clipboard.write(person.formatted_customer_number)
    safe_notify("Kundennummer kopiert.")


def _customer_number_row(person: Person, *, label: str = "Kunden-Nr.", classes: str = "text-caption text-grey-6") -> None:
    """Render the Kunden-Nr. label with an inline copy-to-clipboard button.

    Args:
        person: Person whose customer number to show.
        label: Text preceding the formatted number (e.g. "Kunden-Nr." or
            "Kunden-Nr.:", to match the two slightly different label
            styles used on the list and detail pages).
        classes: CSS classes applied to the label itself.

    Returns:
        None.
    """
    with ui.row().classes("items-center gap-1"):
        ui.label(f"{label} {person.formatted_customer_number}").classes(classes)
        ui.button(icon="content_copy", on_click=lambda: _copy_customer_number(person)).props(
            "dense flat size=sm"
        ).tooltip("Kundennummer kopieren")


#: `(label, field)` pairs for the printed table.
PRINT_COLUMNS = [
    ("Kunden-Nr.", "customer_number"),
    ("Name", "name"),
    ("E-Mail", "email"),
    ("Telefon", "phone"),
    ("Rechnungsadresse", "address"),
    ("IBAN", "iban"),
    ("Status", "status"),
]

DETAIL_COLUMNS = [
    {"name": "designation", "label": "Messpunkt", "field": "designation", "align": "left"},
    {"name": "direction", "label": "Messrichtung", "field": "direction", "align": "left"},
    {"name": "site_address", "label": "Standort-Adresse", "field": "site_address", "align": "left"},
    {"name": "substation_area", "label": "Trafokreis", "field": "substation_area", "align": "left"},
    {"name": "leg", "label": "LEG", "field": "leg", "align": "left"},
    {"name": "valid_from", "label": "Gültig von", "field": "valid_from", "align": "left"},
    {"name": "valid_to", "label": "Gültig bis", "field": "valid_to", "align": "left"},
]


def _print_row(person: Person) -> dict:
    """Convert a `Person` into a row dict for the printed table.

    Args:
        person: Person to convert.

    Returns:
        A dict with the fields required by `PRINT_COLUMNS`.
    """
    return {
        "customer_number": person.formatted_customer_number,
        "name": person.display_name,
        "email": person.contact_email,
        "phone": person.contact_phone,
        "address": (
            f"{person.billing_street_with_number}, "
            f"{person.billing_postal_code} {person.billing_city}"
        ),
        "iban": format_iban(person.iban) if person.iban else "",
        "status": "Aktiv" if person.active else "Inaktiv",
    }


def _search_text_for_person(connection, person: Person) -> str:
    """Build the lowercase substring-search haystack for one Person.

    Covers the person's own fields plus the designation and site
    address of every MeteringPoint ever assigned to them (project prompt
    section 8: persons search also reaches into their assignments).

    Args:
        connection: Open SQLite connection.
        person: Person to index.

    Returns:
        A single lowercase string containing all searchable text.
    """
    parts = [
        person.company,
        person.first_name,
        person.last_name,
        person.contact_email,
        person.contact_phone,
        person.billing_street,
        person.billing_house_number,
        person.billing_postal_code,
        person.billing_city,
        person.formatted_customer_number,
        # Also index the customer number without its grouping space, so a
        # search entered without spaces (e.g. pasted from elsewhere) still
        # matches the formatted "XXX XXX" display value.
        str(person.customer_number) if person.customer_number is not None else "",
        str(person.bkw_customer_number) if person.bkw_customer_number is not None else "",
    ]
    for z in assignment_repo.list_for_person(connection, person.id):
        mp = metering_point_repo.get(connection, z.metering_point_id)
        if mp is None:
            continue
        parts.append(mp.designation)
        site = site_repo.get(connection, mp.site_id)
        if site is not None:
            parts.append(site.full_address)
    return " ".join(p for p in parts if p).lower()


@ui.page("/persons")
def persons_page() -> None:
    """Render the persons list page with search, CRUD, and a link to each detail view.

    Returns:
        None.
    """
    with page_frame("/persons", "Personen"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Personen oder Firmen, die an der LEG teilnehmen (Bezüger, "
                "Produzenten oder beides). Die Zuordnung zu Messpunkten "
                "erfolgt unter „Zuordnungen“. Die Kunden-Nr. wird beim "
                "Anlegen automatisch und eindeutig vergeben."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    rubrik="Personen",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: [_print_row(p) for p in visible_persons],
                    get_filter_description=lambda: _filter_description(),
                )
                ui.button(
                    "+ Neue Person", on_click=lambda: open_person_form(on_saved=lambda _: refresh())
                )

        with ui.row().classes("w-full items-center gap-4"):
            search_input = ui.input("Suche (Name, Firma, Kunden-Nr., Kontakt, Adresse, Messpunkt...)").classes(
                "w-full max-w-md"
            ).props("debounce=300 clearable")
            show_inactive_switch = ui.switch("Deaktivierte Personen anzeigen")

        list_container = ui.column().classes("w-full gap-2 mt-2")

        all_entries: list[tuple[Person, str]] = []
        visible_persons: list[Person] = []

        def _filter_description() -> str | None:
            """Build a short description of the currently active search/filter.

            Returns:
                A human-readable summary, or `None` if no filter is active.
            """
            parts = []
            if search_input.value:
                parts.append(f'Suche: "{search_input.value.strip()}"')
            if show_inactive_switch.value:
                parts.append("inkl. deaktivierte Personen")
            return ", ".join(parts) if parts else None

        def render_card(person: Person) -> None:
            """Render one Person as a card with wrapping field groups.

            Args:
                person: Person to render.

            Returns:
                None.
            """
            with ui.card().classes("w-full" + ("" if person.active else " opacity-60")):
                with ui.row().classes("w-full items-start gap-6 flex-wrap"):
                    with ui.column().classes("gap-0 min-w-[200px]"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(person.display_name).classes("font-bold")
                            if not person.active:
                                ui.badge("Inaktiv", color="grey")
                        _customer_number_row(person)
                        if person.bkw_customer_number is not None:
                            ui.label(f"BKW-Kunden-Nr. {person.bkw_customer_number}").classes(
                                "text-caption text-grey-6"
                            )
                    with ui.column().classes("gap-0 min-w-[180px]"):
                        ui.label(person.contact_email or "-")
                        ui.label(person.contact_phone or "-").classes("text-grey-7")
                    with ui.column().classes("gap-0 min-w-[220px]"):
                        ui.label(person.billing_street_with_number or "-")
                        ui.label(
                            f"{person.billing_postal_code} {person.billing_city}".strip()
                        )
                    with ui.column().classes("gap-0 min-w-[200px]"):
                        ui.label(f"IBAN: {format_iban(person.iban) if person.iban else '-'}")
                        ui.label(
                            "Papierrechnung: " + ("ja" if person.paper_invoice else "nein")
                        ).classes("text-grey-7")
                    with ui.row().classes("gap-1 ml-auto"):
                        ui.button(icon="visibility", on_click=lambda: on_view(person)).props("dense flat")
                        ui.button(icon="edit", on_click=lambda: on_edit(person)).props("dense flat")
                        if person.active:
                            ui.button(icon="delete", on_click=lambda: on_remove(person)).props(
                                "dense flat color=negative"
                            )
                        else:
                            ui.button(
                                icon="restore", on_click=lambda: on_reactivate(person)
                            ).props("dense flat color=primary").tooltip("Wieder aktivieren")

        def apply_filter() -> None:
            """Filter the currently loaded persons by search text and active state.

            Deactivated persons are hidden by default -- "weg ist weg" --
            and only shown if `show_inactive_switch` is toggled on.

            Returns:
                None.
            """
            nonlocal visible_persons
            needle = (search_input.value or "").strip().lower()
            visible_persons = [
                person
                for person, search_text in all_entries
                if (person.active or show_inactive_switch.value) and (not needle or needle in search_text)
            ]
            list_container.clear()
            with list_container:
                for person in visible_persons:
                    render_card(person)

        def refresh() -> None:
            """Reload all persons from the database and re-apply the filter.

            Returns:
                None.
            """
            nonlocal all_entries
            with connection_scope() as connection:
                persons = person_repo.list_all(connection)
                all_entries = [(p, _search_text_for_person(connection, p)) for p in persons]
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())
        show_inactive_switch.on_value_change(lambda _: apply_filter())

        def on_view(person: Person) -> None:
            """Card view-button handler: navigate to the person's detail page.

            Args:
                person: Person whose detail page to open.

            Returns:
                None.
            """
            ui.navigate.to(f"/persons/{person.id}")

        def on_edit(person: Person) -> None:
            """Card edit-button handler: open the edit dialog for this person.

            Args:
                person: Person to edit.

            Returns:
                None.
            """
            with connection_scope() as connection:
                existing = person_repo.get(connection, person.id)
            open_person_form(existing=existing, on_saved=lambda _: refresh())

        def on_remove(person: Person) -> None:
            """Card delete-button handler: delete the person after confirmation.

            If the person still has billing history, they are deactivated
            instead of deleted (see `person_repo.delete`) -- their
            customer number and Abrechnungshistorie stay intact, but they are
            hidden from selection for new assignments.

            Args:
                person: Person to delete.

            Returns:
                None.
            """
            with ui.dialog() as confirm, ui.card():
                ui.label(f'"{person.display_name}" wirklich löschen?')
                ui.label(
                    "Falls bereits Abrechnungen für diese Person bestehen, "
                    "wird sie stattdessen nur deaktiviert (nicht gelöscht) -- "
                    "sie bleibt für Buchhaltung und Statistik erhalten, "
                    "erscheint aber nicht mehr zur Auswahl bei neuen "
                    "Zuordnungen."
                ).classes("text-caption text-grey-7")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            deleted = person_repo.delete(connection, person.id)
                        confirm.close()
                        # notify before refresh() -- see save() above for why
                        if deleted:
                            safe_notify("Gelöscht.", type="warning")
                        else:
                            safe_notify(
                                "Es bestehen bereits Abrechnungsbelege für diese "
                                "Person -- sie wurde deaktiviert statt gelöscht.",
                                type="warning",
                            )
                        refresh()

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        def on_reactivate(person: Person) -> None:
            """Card reactivate-button handler: mark a deactivated person active again.

            Args:
                person: Person to reactivate.

            Returns:
                None.
            """
            with connection_scope() as connection:
                person_repo.set_active(connection, person.id, True)
            # notify before refresh() -- see save() above for why
            safe_notify("Person wieder aktiviert.", type="positive")
            refresh()

        refresh()


@ui.page("/persons/{person_id}")
def person_detail_page(person_id: int) -> None:
    """Render one person's detail view: Stammdaten plus their assignment history.

    Args:
        person_id: Database id of the person, from the URL path.

    Returns:
        None.
    """
    with connection_scope() as connection:
        person = person_repo.get(connection, person_id)

    with page_frame("/persons", "Person" if person is None else person.display_name):
        if person is None:
            ui.label("Person nicht gefunden.").classes("text-negative")
            ui.link("← Zurück zu Personen", "/persons")
            return

        ui.link("← Zurück zu Personen", "/persons")
        ui.label(person.display_name).classes("text-xl font-bold mt-2")
        with ui.card().classes("w-full max-w-lg"):
            if not person.active:
                ui.label("Status: Inaktiv (deaktiviert)").classes("text-negative")
            _customer_number_row(person, label="Kunden-Nr.:", classes="")
            if person.bkw_customer_number is not None:
                ui.label(f"BKW-Kundennummer: {person.bkw_customer_number}")
            if person.company:
                ui.label(f"Firma: {person.company}")
            ui.label(f"Anrede: {person.salutation or '-'}")
            ui.label(f"Vorname/Nachname: {person.full_name or '-'}")
            ui.label(f"E-Mail: {person.contact_email or '-'}")
            ui.label(f"Telefon: {person.contact_phone or '-'}")
            ui.label(
                "Rechnungsadresse: "
                f"{person.billing_street_with_number}, "
                f"{person.billing_postal_code} {person.billing_city} "
                f"({person.billing_country})"
            )
            ui.label(f"IBAN: {format_iban(person.iban) if person.iban else '-'}")
            ui.label(f"Papierrechnung: {'ja' if person.paper_invoice else 'nein'}")

        with connection_scope() as connection:
            onboarding = person_onboarding_repo.get_by_person(connection, person_id)
            onboarding_threshold = settings_repo.get_settings(connection).onboarding_overdue_days

        if onboarding is not None:
            ui.label("Aufnahmeprozess").classes("text-lg font-bold mt-6")
            onboarding_card = ui.column().classes("w-full max-w-lg")

            def render_onboarding_status() -> None:
                """(Re-)render the onboarding status card from the current
                (possibly just-edited) `onboarding` object.

                Returns:
                    None.
                """
                onboarding_card.clear()
                with onboarding_card, ui.card().classes("w-full"):
                    if onboarding.is_complete:
                        ui.label("✓ Abgeschlossen").classes("text-positive")
                    else:
                        _, step_label = onboarding.current_step
                        overdue = onboarding.is_overdue(onboarding_threshold)
                        ui.label(
                            f"Aktueller Schritt: {step_label} "
                            f"(seit {onboarding.days_open()} Tagen)"
                        ).classes("text-negative" if overdue else "")
                    ui.button(
                        "Bearbeiten",
                        on_click=lambda: open_onboarding_form(
                            onboarding, person, on_saved=lambda _: render_onboarding_status()
                        ),
                    ).props("dense flat").classes("mt-2")

            render_onboarding_status()

        with connection_scope() as connection:
            offboarding = person_offboarding_repo.get_by_person(connection, person_id)

        if offboarding is not None:
            ui.label("Austritts-/Ausschlussprozess").classes("text-lg font-bold mt-6")
            offboarding_card = ui.column().classes("w-full max-w-lg")

            def render_offboarding_status() -> None:
                """(Re-)render the offboarding status card from the current
                (possibly just-edited) `offboarding` object.

                Returns:
                    None.
                """
                offboarding_card.clear()
                with offboarding_card, ui.card().classes("w-full"):
                    ui.label(f"Grund: {REASON_OPTIONS.get(offboarding.reason, offboarding.reason)}").classes(
                        "text-caption text-grey-6"
                    )
                    if offboarding.is_complete:
                        ui.label("✓ Abgeschlossen").classes("text-positive")
                    else:
                        _, step_label = offboarding.current_step
                        ui.label(f"Aktueller Schritt: {step_label} (seit {offboarding.days_open()} Tagen)")
                    ui.button(
                        "Bearbeiten",
                        on_click=lambda: open_offboarding_form(
                            offboarding, person, on_saved=lambda _: render_offboarding_status()
                        ),
                    ).props("dense flat").classes("mt-2")

            render_offboarding_status()

        ui.label("Zugeordnete Messpunkte").classes("text-lg font-bold mt-6")
        show_all_switch = ui.switch("alle anzeigen (inkl. Historie)")
        leg_warnings_column = ui.column().classes("w-full")
        detail_table = ui.table(columns=DETAIL_COLUMNS, rows=[], row_key="id").classes("w-full mt-2")
        detail_table.add_slot(
            "body-cell-valid_from",
            r'''
            <q-td :props="props" :class="props.row.is_future ? 'text-orange-8' : ''">
                {{ props.value }}
            </q-td>
            ''',
        )

        def refresh_detail() -> None:
            """Reload the person's Assignment → MeteringPoint (→ LEG, → site
            → substation area) join, and warn if any involved LEG mixes
            substation areas.

            Filters to only current-or-upcoming assignments (not yet
            ended, `valid_from` may lie in the future -- see
            `app.models.assignment.Assignment.is_current_or_upcoming`)
            unless `show_all_switch` is on, which also shows past,
            already-ended ones ("Historie"). `valid_from`/`valid_to`
            are shown as explicit columns, so a not-yet-started row is
            still distinguishable without extra marking.

            Returns:
                None.
            """
            today = date.today()
            with connection_scope() as inner_connection:
                assignments = assignment_repo.list_for_person(inner_connection, person_id)
                rows = []
                leg_ids_involved: set[int] = set()
                for z in assignments:
                    is_relevant = z.valid_to is None or z.valid_to >= today
                    if not show_all_switch.value and not is_relevant:
                        continue
                    mp = metering_point_repo.get(inner_connection, z.metering_point_id)
                    site = (
                        site_repo.get(inner_connection, mp.site_id) if mp else None
                    )
                    substation_area = (
                        substation_area_repo.get(inner_connection, site.substation_area_id)
                        if site and site.substation_area_id
                        else None
                    )
                    leg = (
                        leg_repo.get(inner_connection, mp.leg_id) if mp and mp.leg_id else None
                    )
                    if leg is not None:
                        leg_ids_involved.add(leg.id)
                    rows.append(
                        {
                            "id": z.id,
                            "designation": mp.designation if mp else "?",
                            "direction": DIRECTION_LABELS.get(mp.direction, mp.direction)
                            if mp
                            else "?",
                            "site_address": site.full_address if site else "?",
                            "substation_area": substation_area.name if substation_area else "-",
                            "leg": leg.name if leg else "-",
                            "valid_from": z.valid_from.isoformat(),
                            "valid_to": z.valid_to.isoformat() if z.valid_to else "offen",
                            "is_future": z.valid_from > today,
                        }
                    )
                mixed_warnings = []
                for leg_id in sorted(leg_ids_involved):
                    composition = compute_leg_composition(inner_connection, leg_id)
                    if not composition.is_mixed:
                        continue
                    leg = leg_repo.get(inner_connection, leg_id)
                    substation_area_names = ", ".join(t.name for t in composition.substation_areas)
                    mixed_warnings.append(
                        f"⚠ Die LEG „{leg.name}“ dieser Person umfasst mehrere "
                        f"Trafokreise ({substation_area_names}) -- die BKW gewährt "
                        "dafür vermutlich einen tieferen Rabatt. Informieren "
                        "Sie die Person ggf. darüber."
                    )
            detail_table.rows = rows
            detail_table.update()
            leg_warnings_column.clear()
            with leg_warnings_column:
                for message in mixed_warnings:
                    ui.label(message).classes("text-warning text-body2")

        show_all_switch.on_value_change(lambda _: refresh_detail())
        refresh_detail()
