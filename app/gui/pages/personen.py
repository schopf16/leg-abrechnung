"""Personen management page: list, search, create, edit, delete, and a
detail drill-down showing the Person → Zuordnung → Messpunkt (→ LEG,
→ Standort → Trafokreis) join (project prompt section 7,
"Personen-Detailansicht").

The list is rendered as one card per Person (not a single-row-per-person
table): a Person has enough fields (Name/Firma, Kontakt, Rechnungsadresse,
IBAN) that a flat table forces horizontal scrolling. Cards let each group
of fields wrap onto its own line instead.
"""

from datetime import date

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.iban_validation import format_iban, normalize_iban, validate_iban
from app.domain.leg_composition import compute_leg_composition
from app.gui.navigation import page_frame
from app.gui.safe_notify import safe_notify
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import standort as standort_repo
from app.models import trafokreis as trafokreis_repo
from app.models import zuordnung as zuordnung_repo
from app.models.messpunkt import MESSRICHTUNG_BEZUG, MESSRICHTUNG_EINSPEISUNG
from app.models.person import ANREDE_OPTIONS, Person

MESSRICHTUNG_LABELS = {
    MESSRICHTUNG_BEZUG: "Bezug",
    MESSRICHTUNG_EINSPEISUNG: "Einspeisung",
}

DETAIL_COLUMNS = [
    {"name": "messpunkt_bezeichnung", "label": "Messpunkt", "field": "messpunkt_bezeichnung", "align": "left"},
    {"name": "messrichtung", "label": "Messrichtung", "field": "messrichtung", "align": "left"},
    {"name": "standort_adresse", "label": "Standort-Adresse", "field": "standort_adresse", "align": "left"},
    {"name": "trafokreis", "label": "Trafokreis", "field": "trafokreis", "align": "left"},
    {"name": "leg", "label": "LEG", "field": "leg", "align": "left"},
    {"name": "gueltig_von", "label": "Gültig von", "field": "gueltig_von", "align": "left"},
    {"name": "gueltig_bis", "label": "Gültig bis", "field": "gueltig_bis", "align": "left"},
]


def _search_text_for_person(connection, person: Person) -> str:
    """Build the lowercase substring-search haystack for one Person.

    Covers the person's own fields plus the designation and Standort
    address of every Messpunkt ever assigned to them (project prompt
    section 8: Personen search also reaches into their Zuordnungen).

    Args:
        connection: Open SQLite connection.
        person: Person to index.

    Returns:
        A single lowercase string containing all searchable text.
    """
    parts = [
        person.firma,
        person.vorname,
        person.nachname,
        person.kontakt_email,
        person.kontakt_telefon,
        person.rechnungsadresse_strasse,
        person.rechnungsadresse_hausnummer,
        person.rechnungsadresse_plz,
        person.rechnungsadresse_ort,
        person.kundennummer_formatiert,
        str(person.bkw_kundennummer) if person.bkw_kundennummer is not None else "",
    ]
    for z in zuordnung_repo.list_for_person(connection, person.id):
        mp = messpunkt_repo.get(connection, z.messpunkt_id)
        if mp is None:
            continue
        parts.append(mp.messpunkt_bezeichnung)
        standort = standort_repo.get(connection, mp.standort_id)
        if standort is not None:
            parts.append(standort.adresse_vollstaendig)
    return " ".join(p for p in parts if p).lower()


@ui.page("/personen")
def personen_page() -> None:
    """Render the Personen list page with search, CRUD, and a link to each detail view.

    Returns:
        None.
    """
    with page_frame("/personen", "Personen"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Personen oder Firmen, die an der LEG teilnehmen (Bezüger, "
                "Produzenten oder beides). Die Zuordnung zu Messpunkten "
                "erfolgt unter „Zuordnungen“. Die Kunden-Nr. wird beim "
                "Anlegen automatisch und eindeutig vergeben."
            ).classes("text-body2 text-grey-8")
            ui.button("+ Neue Person", on_click=lambda: open_form(None)).classes("shrink-0")

        with ui.row().classes("w-full items-center gap-4"):
            search_input = ui.input("Suche (Name, Firma, Kunden-Nr., Kontakt, Adresse, Messpunkt...)").classes(
                "w-full max-w-md"
            ).props("debounce=300 clearable")
            show_inactive_switch = ui.switch("Deaktivierte Personen anzeigen")

        list_container = ui.column().classes("w-full gap-2 mt-2")

        all_entries: list[tuple[Person, str]] = []

        def render_card(person: Person) -> None:
            """Render one Person as a card with wrapping field groups.

            Args:
                person: Person to render.

            Returns:
                None.
            """
            with ui.card().classes("w-full" + ("" if person.aktiv else " opacity-60")):
                with ui.row().classes("w-full items-start gap-6 flex-wrap"):
                    with ui.column().classes("gap-0 min-w-[200px]"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(person.anzeige_name).classes("font-bold")
                            if not person.aktiv:
                                ui.badge("Inaktiv", color="grey")
                        ui.label(f"Kunden-Nr. {person.kundennummer_formatiert}").classes(
                            "text-caption text-grey-6"
                        )
                        if person.bkw_kundennummer is not None:
                            ui.label(f"BKW-Kunden-Nr. {person.bkw_kundennummer}").classes(
                                "text-caption text-grey-6"
                            )
                    with ui.column().classes("gap-0 min-w-[180px]"):
                        ui.label(person.kontakt_email or "-")
                        ui.label(person.kontakt_telefon or "-").classes("text-grey-7")
                    with ui.column().classes("gap-0 min-w-[220px]"):
                        ui.label(person.rechnungsadresse_strasse_vollstaendig or "-")
                        ui.label(
                            f"{person.rechnungsadresse_plz} {person.rechnungsadresse_ort}".strip()
                        )
                    with ui.column().classes("gap-0 min-w-[200px]"):
                        ui.label(f"IBAN: {format_iban(person.iban) if person.iban else '-'}")
                        ui.label(
                            "Papierrechnung: " + ("ja" if person.papierrechnung else "nein")
                        ).classes("text-grey-7")
                    with ui.row().classes("gap-1 ml-auto"):
                        ui.button(icon="visibility", on_click=lambda: on_view(person)).props("dense flat")
                        ui.button(icon="edit", on_click=lambda: on_edit(person)).props("dense flat")
                        if person.aktiv:
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
            needle = (search_input.value or "").strip().lower()
            list_container.clear()
            with list_container:
                for person, search_text in all_entries:
                    if not person.aktiv and not show_inactive_switch.value:
                        continue
                    if not needle or needle in search_text:
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

        def open_form(existing: Person | None) -> None:
            """Open the create/edit dialog for a person.

            Args:
                existing: Person to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
                ui.label("Person bearbeiten" if existing else "Neue Person").classes(
                    "text-lg font-bold"
                )
                firma = ui.input(
                    "Firma (optional -- leer lassen für eine Privatperson)",
                    value=existing.firma if existing else "",
                ).classes("w-full")
                ui.label(
                    "Vorname/Nachname: der Person selbst, oder der "
                    "Ansprechsperson bei einer Firma (kann bei einer reinen "
                    "Firmenadresse ohne Ansprechsperson leer bleiben)."
                ).classes("text-caption text-grey-6")
                with ui.row().classes("w-full gap-2"):
                    anrede = ui.select(
                        ["", *ANREDE_OPTIONS],
                        label="Anrede",
                        value=existing.anrede if existing else "",
                    ).classes("w-32")
                    vorname = ui.input(
                        "Vorname", value=existing.vorname if existing else ""
                    ).classes("flex-grow")
                    nachname = ui.input(
                        "Nachname", value=existing.nachname if existing else ""
                    ).classes("flex-grow")
                with ui.row().classes("w-full gap-2"):
                    strasse = ui.input(
                        "Adresse: Strasse",
                        value=existing.rechnungsadresse_strasse if existing else "",
                    ).classes("flex-grow")
                    hausnummer = ui.input(
                        "Hausnummer", value=existing.rechnungsadresse_hausnummer if existing else ""
                    ).classes("w-24")
                with ui.row().classes("w-full gap-2"):
                    plz = ui.input(
                        "PLZ", value=existing.rechnungsadresse_plz if existing else ""
                    ).classes("w-24")
                    ort = ui.input(
                        "Ort", value=existing.rechnungsadresse_ort if existing else ""
                    ).classes("flex-grow")
                    land = ui.input(
                        "Land", value=existing.rechnungsadresse_land if existing else "CH"
                    ).classes("w-24")

                ui.separator().classes("my-2")
                ui.label("Weitere Angaben").classes("text-body1 font-bold")
                with ui.row().classes("w-full gap-2"):
                    email = ui.input(
                        "E-Mail", value=existing.kontakt_email if existing else ""
                    ).classes("flex-grow")
                    telefon = ui.input(
                        "Telefon (optional)", value=existing.kontakt_telefon if existing else ""
                    ).classes("flex-grow")
                with ui.row().classes("w-full gap-2"):
                    iban = ui.input(
                        "IBAN (für Gutschriften)", value=existing.iban if existing else ""
                    ).classes("flex-grow")
                    bkw_kundennummer = ui.number(
                        "BKW-Kundennummer (optional)",
                        value=existing.bkw_kundennummer if existing else None,
                        format="%.0f",
                    ).classes("w-48")
                iban_error = ui.label("").classes("text-negative text-caption")

                def check_iban() -> None:
                    """Validate the IBAN once the field loses focus (not on every keystroke).

                    Returns:
                        None.
                    """
                    iban_error.text = validate_iban(iban.value) or ""

                iban.on("blur", check_iban)
                papierrechnung = ui.checkbox(
                    "Papierrechnung (statt elektronisch, kostenpflichtig)",
                    value=existing.papierrechnung if existing else False,
                )
                if existing:
                    ui.label(
                        f"Kunden-Nr.: {existing.kundennummer_formatiert} "
                        "(automatisch vergeben, nicht änderbar)"
                    ).classes("text-caption text-grey-6")
                else:
                    ui.label(
                        "Die Kunden-Nr. wird beim Speichern automatisch und "
                        "zufällig vergeben (keine fortlaufende Nummer, um "
                        "Rückschlüsse auf Kundenanzahl oder -reihenfolge zu "
                        "verhindern) und ist danach nicht mehr änderbar."
                    ).classes("text-caption text-grey-6")
                error_label = ui.label("").classes("text-negative")

                def save() -> None:
                    """Validate the form and persist the person.

                    Returns:
                        None.
                    """
                    if not firma.value.strip() and not (vorname.value.strip() or nachname.value.strip()):
                        error_label.text = "Firma oder Vorname/Nachname sind erforderlich."
                        return
                    iban_problem = validate_iban(iban.value)
                    if iban_problem:
                        iban_error.text = iban_problem
                        error_label.text = iban_problem
                        return
                    iban_normalized = normalize_iban(iban.value)
                    with connection_scope() as connection:
                        if existing:
                            updated = Person(
                                id=existing.id,
                                anrede=anrede.value or "",
                                firma=firma.value.strip(),
                                vorname=vorname.value.strip(),
                                nachname=nachname.value.strip(),
                                kontakt_email=email.value.strip(),
                                kontakt_telefon=telefon.value.strip(),
                                rechnungsadresse_strasse=strasse.value.strip(),
                                rechnungsadresse_hausnummer=hausnummer.value.strip(),
                                rechnungsadresse_plz=plz.value.strip(),
                                rechnungsadresse_ort=ort.value.strip(),
                                rechnungsadresse_land=land.value.strip() or "CH",
                                iban=iban_normalized,
                                kundennummer=existing.kundennummer,
                                bkw_kundennummer=int(bkw_kundennummer.value)
                                if bkw_kundennummer.value is not None
                                else None,
                                papierrechnung=papierrechnung.value,
                                aktiv=existing.aktiv,
                                created_at=existing.created_at,
                            )
                            person_repo.update(connection, updated)
                        else:
                            new_person = Person(
                                id=None,
                                anrede=anrede.value or "",
                                firma=firma.value.strip(),
                                vorname=vorname.value.strip(),
                                nachname=nachname.value.strip(),
                                kontakt_email=email.value.strip(),
                                kontakt_telefon=telefon.value.strip(),
                                rechnungsadresse_strasse=strasse.value.strip(),
                                rechnungsadresse_hausnummer=hausnummer.value.strip(),
                                rechnungsadresse_plz=plz.value.strip(),
                                rechnungsadresse_ort=ort.value.strip(),
                                rechnungsadresse_land=land.value.strip() or "CH",
                                iban=iban_normalized,
                                kundennummer=None,
                                bkw_kundennummer=int(bkw_kundennummer.value)
                                if bkw_kundennummer.value is not None
                                else None,
                                papierrechnung=papierrechnung.value,
                                aktiv=True,
                                created_at="",
                            )
                            person_repo.create(connection, new_person)
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

        def on_view(person: Person) -> None:
            """Card view-button handler: navigate to the person's detail page.

            Args:
                person: Person whose detail page to open.

            Returns:
                None.
            """
            ui.navigate.to(f"/personen/{person.id}")

        def on_edit(person: Person) -> None:
            """Card edit-button handler: open the edit dialog for this person.

            Args:
                person: Person to edit.

            Returns:
                None.
            """
            with connection_scope() as connection:
                existing = person_repo.get(connection, person.id)
            open_form(existing)

        def on_remove(person: Person) -> None:
            """Card delete-button handler: delete the person after confirmation.

            If the person still has billing history, they are deactivated
            instead of deleted (see `person_repo.delete`) -- their
            Kundennummer and Abrechnungshistorie stay intact, but they are
            hidden from selection for new Zuordnungen.

            Args:
                person: Person to delete.

            Returns:
                None.
            """
            with ui.dialog() as confirm, ui.card():
                ui.label(f'"{person.anzeige_name}" wirklich löschen?')
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
                person_repo.set_aktiv(connection, person.id, True)
            # notify before refresh() -- see save() above for why
            safe_notify("Person wieder aktiviert.", type="positive")
            refresh()

        refresh()


@ui.page("/personen/{person_id}")
def person_detail_page(person_id: int) -> None:
    """Render one person's detail view: Stammdaten plus their Zuordnungshistorie.

    Args:
        person_id: Database id of the person, from the URL path.

    Returns:
        None.
    """
    with connection_scope() as connection:
        person = person_repo.get(connection, person_id)

    with page_frame("/personen", "Person" if person is None else person.anzeige_name):
        if person is None:
            ui.label("Person nicht gefunden.").classes("text-negative")
            ui.link("← Zurück zu Personen", "/personen")
            return

        ui.link("← Zurück zu Personen", "/personen")
        ui.label(person.anzeige_name).classes("text-xl font-bold mt-2")
        with ui.card().classes("w-full max-w-lg"):
            if not person.aktiv:
                ui.label("Status: Inaktiv (deaktiviert)").classes("text-negative")
            ui.label(f"Kunden-Nr.: {person.kundennummer_formatiert}")
            if person.bkw_kundennummer is not None:
                ui.label(f"BKW-Kundennummer: {person.bkw_kundennummer}")
            if person.firma:
                ui.label(f"Firma: {person.firma}")
            ui.label(f"Anrede: {person.anrede or '-'}")
            ui.label(f"Vorname/Nachname: {person.voller_name or '-'}")
            ui.label(f"E-Mail: {person.kontakt_email or '-'}")
            ui.label(f"Telefon: {person.kontakt_telefon or '-'}")
            ui.label(
                "Rechnungsadresse: "
                f"{person.rechnungsadresse_strasse_vollstaendig}, "
                f"{person.rechnungsadresse_plz} {person.rechnungsadresse_ort} "
                f"({person.rechnungsadresse_land})"
            )
            ui.label(f"IBAN: {format_iban(person.iban) if person.iban else '-'}")
            ui.label(f"Papierrechnung: {'ja' if person.papierrechnung else 'nein'}")

        ui.label("Zugeordnete Messpunkte").classes("text-lg font-bold mt-6")
        show_all_switch = ui.switch("alle anzeigen (inkl. Historie)")
        leg_warnings_column = ui.column().classes("w-full")
        detail_table = ui.table(columns=DETAIL_COLUMNS, rows=[], row_key="id").classes("w-full mt-2")

        def refresh_detail() -> None:
            """Reload the person's Zuordnung → Messpunkt (→ LEG, → Standort
            → Trafokreis) join, and warn if any involved LEG mixes
            Trafokreise.

            Filters to only currently valid Zuordnungen unless
            `show_all_switch` is on.

            Returns:
                None.
            """
            today = date.today()
            with connection_scope() as inner_connection:
                zuordnungen = zuordnung_repo.list_for_person(inner_connection, person_id)
                rows = []
                leg_ids_involved: set[int] = set()
                for z in zuordnungen:
                    is_current = z.gueltig_von <= today and (
                        z.gueltig_bis is None or z.gueltig_bis >= today
                    )
                    if not show_all_switch.value and not is_current:
                        continue
                    mp = messpunkt_repo.get(inner_connection, z.messpunkt_id)
                    standort = (
                        standort_repo.get(inner_connection, mp.standort_id) if mp else None
                    )
                    trafokreis = (
                        trafokreis_repo.get(inner_connection, standort.trafokreis_id)
                        if standort and standort.trafokreis_id
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
                            "messpunkt_bezeichnung": mp.messpunkt_bezeichnung if mp else "?",
                            "messrichtung": MESSRICHTUNG_LABELS.get(mp.messrichtung, mp.messrichtung)
                            if mp
                            else "?",
                            "standort_adresse": standort.adresse_vollstaendig if standort else "?",
                            "trafokreis": trafokreis.name if trafokreis else "-",
                            "leg": leg.name if leg else "-",
                            "gueltig_von": z.gueltig_von.isoformat(),
                            "gueltig_bis": z.gueltig_bis.isoformat() if z.gueltig_bis else "offen",
                        }
                    )
                mixed_warnings = []
                for leg_id in sorted(leg_ids_involved):
                    composition = compute_leg_composition(inner_connection, leg_id)
                    if not composition.is_mixed:
                        continue
                    leg = leg_repo.get(inner_connection, leg_id)
                    trafokreis_names = ", ".join(t.name for t in composition.trafokreise)
                    mixed_warnings.append(
                        f"⚠ Die LEG „{leg.name}“ dieser Person umfasst mehrere "
                        f"Trafokreise ({trafokreis_names}) -- die BKW gewährt "
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
