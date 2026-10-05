"""persons management page: list, search, create, edit, delete, and a detail drill-down showing the
Person → Assignment → MeteringPoint (→ LEG, → site → substation area) join (project prompt section
7, "persons-Detailansicht")."""

from datetime import date

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.iban_validation import format_iban
from app.domain.leg_composition import compute_leg_composition
from app.domain.salutation import letter_salutation
from app.domain.quality_checks import SUBJECT_PERSON
from app.gui.filter_bar import FilterBar
from app.gui.detail_header import render_detail_header
from app.gui.navigation import page_frame
from app.gui.problem_markers import (
    TABLE_MARKER_HTML,
    load_problems,
    render_problem_notes,
)
from app.gui.cooperative_form import render_cooperative_history
from app.gui.offboarding_form import open_offboarding_form
from app.gui.onboarding_form import open_onboarding_form
from app.gui.person_form import open_person_form
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.gui.table_list import paged_table
from app.gui.sorting import (
    SortOption,
    address_key,
    apply_sort,
    number_key,
    person_name_key,
    sort_description,
    text_key,
)
from app.models import cooperative_membership as cooperative_membership_repo
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
    """Copy a person's formatted customer number to the clipboard and confirm."""
    ui.clipboard.write(person.formatted_customer_number)
    safe_notify("Kundennummer kopiert.")


def _customer_number_row(
    person: Person, *, label: str = "Kunden-Nr.", classes: str = "text-caption text-grey-6"
) -> None:
    """Render the Kunden-Nr. label with an inline copy-to-clipboard button."""
    with ui.row().classes("items-center gap-1"):
        ui.label(f"{label} {person.formatted_customer_number}").classes(classes)
        ui.button(icon="content_copy", on_click=lambda: _copy_customer_number(person)).props(
            "dense flat size=sm"
        ).tooltip("Kundennummer kopieren")


#: What the list shows, on the administrator's own choice: "wichtig wäre mir
#: sicher kundennummer, name vielleicht noch adresse? alles andere dann
#: hinter auge". Everything else was already on the detail page.
#:
#: Columns are free here in a way cards never were: a Quasar table is one
#: interface element with its rows as data, so a fourth column costs
#: nothing, while a 23-element card cost that much times 92.
COLUMNS = [
    {"name": "customer_number", "label": "Kunden-Nr.", "field": "customer_number", "align": "left"},
    {"name": "name", "label": "Name", "field": "name", "align": "left"},
    {"name": "address", "label": "Adresse", "field": "address", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]


#: `(label, field)` pairs for the printed table.
PRINT_COLUMNS = [
    ("Kunden-Nr.", "customer_number"),
    ("Anrede", "salutation"),
    ("Name", "name"),
    ("E-Mail", "email"),
    ("Telefon", "phone"),
    ("Rechnungsadresse", "address"),
    ("IBAN", "iban"),
    ("Genossenschafter", "cooperative"),
    ("Anteile", "shares"),
    ("Bemerkung", "note"),
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


#: Orders the Personen list offers, default first. Surname first: that is
#: how the administrator looks somebody up, and it matches the order every
#: other list shows the same people in.
SORT_OPTIONS = [
    SortOption("last_name", "Nachname", person_name_key),
    SortOption(
        "customer_number",
        "Kunden-Nr.",
        lambda person: (number_key(person.customer_number), person_name_key(person)),
    ),
    SortOption(
        "city",
        "Ort",
        lambda person: (
            text_key(person.billing_city),
            address_key(person.billing_street, person.billing_house_number),
            person_name_key(person),
        ),
    ),
    SortOption(
        "status",
        "Status (aktive zuerst)",
        lambda person: (not person.active, person_name_key(person)),
    ),
    # Missing ones first, which is the whole reason this order exists: the
    # administrator sorts by Anrede to find the records that have none,
    # and putting them last would mean scrolling past everyone who is
    # fine. Named like "Status (aktive zuerst)" so the direction is not a
    # surprise.
    SortOption(
        "salutation",
        "Anrede (fehlende zuerst)",
        lambda person: (
            not _missing_salutation(person),
            text_key(person.salutation),
            person_name_key(person),
        ),
    ),
]


def _missing_salutation(person: Person) -> bool:
    """Whether a named person on this record has no salutation."""
    named = person.named_persons
    return bool(named) and any(not one.salutation for one in named)


def _status_text(person: Person) -> str:
    """The Aktiv/Inaktiv text, with the deactivation date when there is one."""
    if person.active:
        return "Aktiv"
    if person.deactivated_at is None:
        return "Inaktiv"
    return f"Inaktiv seit {person.deactivated_at.strftime('%d.%m.%Y')}"


def _print_row(person: Person, membership) -> dict:
    """Convert a `Person` into a row dict for the printed table."""
    return {
        "customer_number": person.formatted_customer_number,
        # Both salutations for a couple, so a printed list shows which half
        # of a pair is missing one.
        "salutation": " / ".join(one.salutation or "?" for one in person.named_persons),
        "name": person.display_name,
        "email": ", ".join(person.contact_emails),
        "phone": person.contact_phone,
        "address": (
            f"{person.billing_street_with_number}, {person.billing_postal_code} {person.billing_city}"
        ),
        "iban": format_iban(person.iban) if person.iban else "",
        "cooperative": "ja" if membership else "nein",
        # Blank rather than "0" for a non-member: zero shares is a real and
        # different state (a member whose shares are not recorded yet, see
        # `app.models.cooperative_membership`).
        "shares": str(membership.shares) if membership else "",
        "note": person.note,
        "status": _status_text(person),
    }


def _search_text_for_person(connection, person: Person) -> str:
    """Build the lowercase substring-search haystack for one Person."""
    parts = [
        person.company,
        person.first_name,
        person.last_name,
        person.second_first_name,
        person.second_last_name,
        person.contact_email,
        person.second_contact_email,
        person.contact_phone,
        person.note,
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
    """Render the persons list page with search, CRUD, and a link to each detail view."""
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
                    heading="Personen",
                    get_columns=lambda: PRINT_COLUMNS,
                    get_rows=lambda: [
                        _print_row(p, memberships_by_person.get(p.id)) for p in visible_persons
                    ],
                    get_filter_description=lambda: _filter_description(),
                    # Named on its own line: a printout is read away from
                    # the screen, where the order is not self-evident.
                    get_sort_description=lambda: sort_description(SORT_OPTIONS, sort_select),
                )
                ui.button("+ Neue Person", on_click=lambda: open_person_form(on_saved=lambda _: refresh()))

        bar = FilterBar("/persons")
        search_input = bar.search("Name, Firma, Kunden-Nr., Kontakt, Adresse, Messpunkt")
        sort_select = bar.sort(SORT_OPTIONS, lambda: apply_filter())
        show_inactive_switch = bar.filter("Deaktivierte Personen anzeigen")
        # The members' list the cooperative needs is this list, filtered
        # and printed -- not a page of its own.
        only_cooperative_switch = bar.filter("Nur Genossenschafter")
        # Scrolling ninety cards to find the handful that are marked is the
        # work this saves. The same control on every list, from
        # `app.gui.problem_markers`, and the bar keeps it below the
        # permanent filters because it comes and goes with the findings.
        problem_filter = bar.problem_filter(lambda: apply_filter())

        table = paged_table(route="/persons", columns=COLUMNS, rows=[], row_key="id").classes("w-full mt-2")
        # The marker comes from `app.gui.problem_markers`: a table renders
        # its cells as markup while a card renders elements, so the triangle
        # exists twice and must not drift. The last button is delete for an
        # active person and restore for a deactivated one, which is why the
        # row carries `is_active`.
        table.add_slot(
            "body-cell-actions",
            f"""
            <q-td :props="props">
                {TABLE_MARKER_HTML}
                <q-btn dense flat icon="visibility" @click="() => $parent.$emit('view', props.row)" />
                <q-btn dense flat icon="edit" @click="() => $parent.$emit('edit', props.row)" />
                <q-btn v-if="props.row.is_active" dense flat icon="delete" color="negative"
                       @click="() => $parent.$emit('remove', props.row)" />
                <q-btn v-else dense flat icon="restore" color="primary"
                       @click="() => $parent.$emit('reactivate', props.row)" />
            </q-td>
            """,
        )

        #: Person ids the address register disagrees with, refreshed with the
        #: list so a correction makes the marker disappear.
        problems: dict = {}

        all_entries: list[tuple[Person, str]] = []
        visible_persons: list[Person] = []
        #: `{person_id: CooperativeMembership}` for everyone who is a member
        #: today -- loaded once per refresh, read by the card, the filter and
        #: the printout, so all three agree.
        memberships_by_person: dict[int, object] = {}

        def _filter_description() -> str | None:
            """Build a short description of the currently active search/filter."""
            parts = []
            if search_input.value:
                parts.append(f'Suche: "{search_input.value.strip()}"')
            if show_inactive_switch.value:
                parts.append("inkl. deaktivierte Personen")
            if only_cooperative_switch.value:
                shares = sum(
                    m.shares for p in visible_persons if (m := memberships_by_person.get(p.id)) is not None
                )
                parts.append(f"nur Genossenschafter ({len(visible_persons)}, {shares} Anteile)")
            return ", ".join(parts) if parts else None

        def row_for(person: Person) -> dict:
            """Describe one person as a table row."""
            locality = f"{person.billing_postal_code} {person.billing_city}".strip()
            address = ", ".join(part for part in (person.billing_street_with_number, locality) if part)
            return {
                "id": person.id,
                "customer_number": person.formatted_customer_number,
                "name": person.display_name + ("" if person.active else f" · {_status_text(person)}"),
                "address": address or "-",
                "is_active": person.active,
                "has_problem": person.id in problems,
            }

        def apply_filter() -> None:
            """Filter the currently loaded persons by search text and active state."""
            nonlocal visible_persons
            needle = (search_input.value or "").strip().lower()
            visible_persons = [
                person
                for person, search_text in all_entries
                if (person.active or show_inactive_switch.value)
                and (not only_cooperative_switch.value or person.id in memberships_by_person)
                and (not problem_filter.active or person.id in problems)
                and (not needle or needle in search_text)
            ]
            visible_persons = apply_sort(visible_persons, SORT_OPTIONS, sort_select)
            # The whole filtered result goes to the table, which shows a
            # window onto it: the search runs over every person and the
            # printout holds every filtered row, not the fifty on screen.
            table.rows = [row_for(person) for person in visible_persons]
            table.update()

        def refresh() -> None:
            """Reload all persons from the database and re-apply the filter."""
            nonlocal all_entries, memberships_by_person
            with connection_scope() as connection:
                persons = person_repo.list_all(connection)
                all_entries = [(p, _search_text_for_person(connection, p)) for p in persons]
                # Today's roll, strictly -- see `app.models.
                # cooperative_membership` on why a membership starting next
                # month does not count yet.
                memberships_by_person = {
                    m.person_id: m
                    for m in cooperative_membership_repo.list_all(connection)
                    if m.covers(date.today())
                }
            problems.clear()
            problems.update(load_problems(SUBJECT_PERSON))
            problem_filter.update(set(problems))
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())
        show_inactive_switch.on_value_change(lambda _: apply_filter())
        only_cooperative_switch.on_value_change(lambda _: apply_filter())

        def on_view(person: Person) -> None:
            """Row view handler: navigate to the person's detail page."""
            ui.navigate.to(f"/persons/{person.id}")

        def on_edit(person: Person) -> None:
            """Row edit handler: open the edit dialog for this person."""
            with connection_scope() as connection:
                existing = person_repo.get(connection, person.id)
            open_person_form(existing=existing, on_saved=lambda _: refresh())

        def on_remove(person: Person) -> None:
            """Row delete handler: delete the person after confirmation."""
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
            """Row reactivate handler: mark a deactivated person active again."""
            with connection_scope() as connection:
                person_repo.set_active(connection, person.id, True)
            # notify before refresh() -- see save() above for why
            safe_notify("Person wieder aktiviert.", type="positive")
            refresh()

        def _person_of(event):
            """Load the person a clicked row stands for."""
            with connection_scope() as connection:
                return person_repo.get(connection, event.args["id"])

        def _with_person(action):
            """Wrap a handler so it receives the person, not the row."""

            def handle(event) -> None:
                """Resolve the row and run the action."""
                person = _person_of(event)
                if person is None:
                    safe_notify("Diese Person gibt es nicht mehr.", type="warning")
                    refresh()
                    return
                action(person)

            return handle

        table.on("view", _with_person(on_view))
        table.on("edit", _with_person(on_edit))
        table.on("remove", _with_person(on_remove))
        table.on("reactivate", _with_person(on_reactivate))

        refresh()


@ui.page("/persons/{person_id}")
def person_detail_page(person_id: int) -> None:
    """Render one person's detail view: Stammdaten plus their assignment history."""
    with connection_scope() as connection:
        person = person_repo.get(connection, person_id)

    with page_frame("/persons", "Person" if person is None else person.display_name):
        if person is None:
            ui.label("Person nicht gefunden.").classes("text-negative")
            ui.link("← Zurück zu Personen", "/persons")
            return

        render_detail_header(
            list_route="/persons",
            list_label="Personen",
            title=person.display_name,
            on_edit=lambda: open_person_form(existing=person, on_saved=lambda _: ui.navigate.reload()),
        )

        # What the triangle in the list withheld: the eye shows it,
        # the pencil fixes it. See `app.gui.problem_markers`.
        render_problem_notes(load_problems(SUBJECT_PERSON).get(person.id))
        ui.label(person.display_name).classes("text-xl font-bold mt-2")
        with ui.card().classes("w-full max-w-lg"):
            if not person.active:
                ui.label(f"Status: {_status_text(person)}").classes("text-negative")
            _customer_number_row(person, label="Kunden-Nr.:", classes="")
            if person.bkw_customer_number is not None:
                ui.label(f"BKW-Kundennummer: {person.bkw_customer_number}")
            if person.company:
                ui.label(f"Firma: {person.company}")
            ui.label(f"Anrede: {person.salutation or '-'}")
            ui.label(f"Vorname/Nachname: {person.full_name or '-'}")
            if person.has_second_person:
                ui.label(f"Zweite Person: {person.second_salutation} {person.second_full_name}".strip())
            ui.label(f"E-Mail: {person.contact_email or '-'}")
            if person.second_contact_email:
                ui.label(f"E-Mail zweite Person: {person.second_contact_email}")
            ui.label(f"Telefon: {person.contact_phone or '-'}")
            ui.label(f"Briefanrede: {letter_salutation(person)}").classes("text-caption text-grey-6")
            ui.label(
                "Rechnungsadresse: "
                f"{person.billing_street_with_number}, "
                f"{person.billing_postal_code} {person.billing_city} "
                f"({person.billing_country})"
            )
            ui.label(f"IBAN: {format_iban(person.iban) if person.iban else '-'}")
            ui.label(f"Papierrechnung: {'ja' if person.paper_invoice else 'nein'}")
            if person.note:
                ui.separator()
                ui.label("Bemerkung (intern)").classes("text-caption text-grey-6")
                ui.label(person.note).classes("whitespace-pre-wrap")

        ui.label("Genossenschaft").classes("text-lg font-bold mt-6")
        with ui.card().classes("w-full max-w-2xl"):
            render_cooperative_history(person.id)

        with connection_scope() as connection:
            onboarding = person_onboarding_repo.get_by_person(connection, person_id)
            onboarding_threshold = settings_repo.get_settings(connection).onboarding_overdue_days

        if onboarding is not None:
            ui.label("Aufnahmeprozess").classes("text-lg font-bold mt-6")
            onboarding_card = ui.column().classes("w-full max-w-lg")

            def render_onboarding_status() -> None:
                """(Re-)render the onboarding status card from the current (possibly just-edited)..."""
                onboarding_card.clear()
                with onboarding_card, ui.card().classes("w-full"):
                    if onboarding.is_complete:
                        ui.label("✓ Abgeschlossen").classes("text-positive")
                    else:
                        _, step_label = onboarding.current_step
                        overdue = onboarding.is_overdue(onboarding_threshold)
                        ui.label(
                            f"Aktueller Schritt: {step_label} (seit {onboarding.days_open()} Tagen)"
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
                """(Re-)render the offboarding status card from the current (possibly just-edited)..."""
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
            r"""
            <q-td :props="props" :class="props.row.is_future ? 'text-orange-8' : ''">
                {{ props.value }}
            </q-td>
            """,
        )

        def refresh_detail() -> None:
            """Reload the person's Assignment → MeteringPoint (→ LEG, → site → substation area) join..."""
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
                    site = site_repo.get(inner_connection, mp.site_id) if mp else None
                    substation_area = (
                        substation_area_repo.get(inner_connection, site.substation_area_id)
                        if site and site.substation_area_id
                        else None
                    )
                    leg = leg_repo.get(inner_connection, mp.leg_id) if mp and mp.leg_id else None
                    if leg is not None:
                        leg_ids_involved.add(leg.id)
                    rows.append(
                        {
                            "id": z.id,
                            "designation": mp.designation if mp else "?",
                            "direction": DIRECTION_LABELS.get(mp.direction, mp.direction) if mp else "?",
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
