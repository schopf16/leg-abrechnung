"""Personen management page: list, search, create, edit, delete, and a
detail drill-down showing the Person → Zuordnung → Messpunkt → Standort
(→ LEG) join (project prompt section 7, "Personen-Detailansicht").
"""

from datetime import date

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.navigation import page_frame
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import standort as standort_repo
from app.models import zuordnung as zuordnung_repo
from app.models.messpunkt import MESSRICHTUNG_BEZUG, MESSRICHTUNG_EINSPEISUNG
from app.models.person import ANREDE_OPTIONS, Person

MESSRICHTUNG_LABELS = {
    MESSRICHTUNG_BEZUG: "Bezug",
    MESSRICHTUNG_EINSPEISUNG: "Einspeisung",
}

COLUMNS = [
    {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
    {"name": "kundennummer", "label": "Kunden-Nr.", "field": "kundennummer", "align": "left"},
    {"name": "kontakt", "label": "Kontakt", "field": "kontakt", "align": "left"},
    {"name": "rechnungsadresse", "label": "Rechnungsadresse", "field": "rechnungsadresse", "align": "left"},
    {"name": "iban", "label": "IBAN", "field": "iban", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]

DETAIL_COLUMNS = [
    {"name": "messpunkt_bezeichnung", "label": "Messpunkt", "field": "messpunkt_bezeichnung", "align": "left"},
    {"name": "messrichtung", "label": "Messrichtung", "field": "messrichtung", "align": "left"},
    {"name": "standort_adresse", "label": "Standort-Adresse", "field": "standort_adresse", "align": "left"},
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
        person.name,
        person.kontakt_email,
        person.kontakt_telefon,
        person.rechnungsadresse_strasse,
        person.rechnungsadresse_plz,
        person.rechnungsadresse_ort,
        person.kundennummer_formatiert,
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


def _to_row(person: Person, search_text: str) -> dict:
    """Convert a `Person` into a row dict for the NiceGUI table.

    Args:
        person: Person to convert.
        search_text: Precomputed haystack from `_search_text_for_person`.

    Returns:
        A dict with the fields required by `COLUMNS`, plus a hidden
        `_search` key used for client-side filtering.
    """
    return {
        "id": person.id,
        "name": person.name,
        "kundennummer": person.kundennummer_formatiert,
        "kontakt": " / ".join(x for x in (person.kontakt_email, person.kontakt_telefon) if x),
        "rechnungsadresse": f"{person.rechnungsadresse_strasse}, "
        f"{person.rechnungsadresse_plz} {person.rechnungsadresse_ort}".strip(", "),
        "iban": person.iban,
        "_search": search_text,
    }


@ui.page("/personen")
def personen_page() -> None:
    """Render the Personen list page with search, CRUD, and a link to each detail view.

    Returns:
        None.
    """
    with page_frame("/personen", "Personen"):
        ui.label(
            "Personen oder Firmen, die an der LEG teilnehmen (Bezüger, "
            "Produzenten oder beides). Die Zuordnung zu Messpunkten "
            "erfolgt unter „Zuordnungen“. Die Kunden-Nr. wird beim "
            "Anlegen automatisch und eindeutig vergeben."
        ).classes("text-body2 text-grey-8")

        search_input = ui.input("Suche (Name, Kunden-Nr., Kontakt, Adresse, Messpunkt...)").classes(
            "w-full max-w-md"
        ).props("debounce=300 clearable")

        table = ui.table(columns=COLUMNS, rows=[], row_key="id").classes("w-full")
        table.add_slot(
            "body-cell-actions",
            r'''
            <q-td :props="props">
                <q-btn dense flat icon="visibility" @click="() => $parent.$emit('view', props.row)" />
                <q-btn dense flat icon="edit" @click="() => $parent.$emit('edit', props.row)" />
                <q-btn dense flat icon="delete" color="negative" @click="() => $parent.$emit('remove', props.row)" />
            </q-td>
            ''',
        )

        all_rows: list[dict] = []

        def apply_filter() -> None:
            """Filter the currently loaded rows by the search input's value.

            Returns:
                None.
            """
            needle = (search_input.value or "").strip().lower()
            table.rows = [r for r in all_rows if needle in r["_search"]] if needle else list(all_rows)
            table.update()

        def refresh() -> None:
            """Reload all persons from the database and re-apply the filter.

            Returns:
                None.
            """
            nonlocal all_rows
            with connection_scope() as connection:
                persons = person_repo.list_all(connection)
                all_rows = [_to_row(p, _search_text_for_person(connection, p)) for p in persons]
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())

        def open_form(existing: Person | None) -> None:
            """Open the create/edit dialog for a person.

            Args:
                existing: Person to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Person bearbeiten" if existing else "Neue Person").classes(
                    "text-lg font-bold"
                )
                if existing:
                    ui.label(f"Kunden-Nr.: {existing.kundennummer_formatiert}").classes(
                        "text-body2 text-grey-8"
                    )
                else:
                    ui.label(
                        "Kunden-Nr. wird beim Speichern automatisch vergeben."
                    ).classes("text-body2 text-grey-8")
                anrede = ui.select(
                    ["", *ANREDE_OPTIONS],
                    label="Anrede",
                    value=existing.anrede if existing else "",
                ).classes("w-full")
                name = ui.input("Name / Firma", value=existing.name if existing else "").classes("w-full")
                email = ui.input(
                    "E-Mail", value=existing.kontakt_email if existing else ""
                ).classes("w-full")
                telefon = ui.input(
                    "Telefon (optional)", value=existing.kontakt_telefon if existing else ""
                ).classes("w-full")
                strasse = ui.input(
                    "Rechnungsadresse: Strasse",
                    value=existing.rechnungsadresse_strasse if existing else "",
                ).classes("w-full")
                with ui.row().classes("w-full gap-2"):
                    plz = ui.input(
                        "PLZ", value=existing.rechnungsadresse_plz if existing else ""
                    ).classes("w-24")
                    ort = ui.input(
                        "Ort", value=existing.rechnungsadresse_ort if existing else ""
                    ).classes("flex-grow")
                land = ui.input(
                    "Land", value=existing.rechnungsadresse_land if existing else "CH"
                ).classes("w-full")
                iban = ui.input(
                    "IBAN (für Gutschriften)", value=existing.iban if existing else ""
                ).classes("w-full")
                papierrechnung = ui.checkbox(
                    "Papierrechnung (statt elektronisch, kostenpflichtig)",
                    value=existing.papierrechnung if existing else False,
                )
                error_label = ui.label("").classes("text-negative")

                def save() -> None:
                    """Validate the form and persist the person.

                    Returns:
                        None.
                    """
                    if not name.value.strip():
                        error_label.text = "Name darf nicht leer sein."
                        return
                    with connection_scope() as connection:
                        if existing:
                            updated = Person(
                                id=existing.id,
                                anrede=anrede.value or "",
                                name=name.value.strip(),
                                kontakt_email=email.value.strip(),
                                kontakt_telefon=telefon.value.strip(),
                                rechnungsadresse_strasse=strasse.value.strip(),
                                rechnungsadresse_plz=plz.value.strip(),
                                rechnungsadresse_ort=ort.value.strip(),
                                rechnungsadresse_land=land.value.strip() or "CH",
                                iban=iban.value.strip(),
                                kundennummer=existing.kundennummer,
                                papierrechnung=papierrechnung.value,
                                created_at=existing.created_at,
                            )
                            person_repo.update(connection, updated)
                        else:
                            new_person = Person(
                                id=None,
                                anrede=anrede.value or "",
                                name=name.value.strip(),
                                kontakt_email=email.value.strip(),
                                kontakt_telefon=telefon.value.strip(),
                                rechnungsadresse_strasse=strasse.value.strip(),
                                rechnungsadresse_plz=plz.value.strip(),
                                rechnungsadresse_ort=ort.value.strip(),
                                rechnungsadresse_land=land.value.strip() or "CH",
                                iban=iban.value.strip(),
                                kundennummer=None,
                                papierrechnung=papierrechnung.value,
                                created_at="",
                            )
                            person_repo.create(connection, new_person)
                    dialog.close()
                    refresh()
                    ui.notify("Gespeichert.", type="positive")

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    ui.button("Speichern", on_click=save)
            dialog.open()

        def on_view(event) -> None:
            """Table row-view handler: navigate to the person's detail page.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            ui.navigate.to(f"/personen/{event.args['id']}")

        def on_edit(event) -> None:
            """Table row-edit handler: open the edit dialog for the clicked row.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            with connection_scope() as connection:
                existing = person_repo.get(connection, event.args["id"])
            open_form(existing)

        def on_remove(event) -> None:
            """Table row-delete handler: delete the person after confirmation.

            Args:
                event: NiceGUI generic event carrying the clicked row's args.

            Returns:
                None.
            """
            person_id = event.args["id"]
            name = event.args["name"]

            with ui.dialog() as confirm, ui.card():
                ui.label(f'"{name}" wirklich löschen?')
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            person_repo.delete(connection, person_id)
                        confirm.close()
                        refresh()
                        ui.notify("Gelöscht.", type="warning")

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("view", on_view)
        table.on("edit", on_edit)
        table.on("remove", on_remove)

        ui.button("+ Neue Person", on_click=lambda: open_form(None)).classes("mt-2")

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

    with page_frame("/personen", "Person" if person is None else person.name):
        if person is None:
            ui.label("Person nicht gefunden.").classes("text-negative")
            ui.link("← Zurück zu Personen", "/personen")
            return

        ui.link("← Zurück zu Personen", "/personen")
        ui.label(person.name).classes("text-xl font-bold mt-2")
        with ui.card().classes("w-full max-w-lg"):
            ui.label(f"Kunden-Nr.: {person.kundennummer_formatiert}")
            ui.label(f"Anrede: {person.anrede or '-'}")
            ui.label(f"E-Mail: {person.kontakt_email or '-'}")
            ui.label(f"Telefon: {person.kontakt_telefon or '-'}")
            ui.label(
                "Rechnungsadresse: "
                f"{person.rechnungsadresse_strasse}, "
                f"{person.rechnungsadresse_plz} {person.rechnungsadresse_ort} "
                f"({person.rechnungsadresse_land})"
            )
            ui.label(f"IBAN: {person.iban or '-'}")
            ui.label(f"Papierrechnung: {'ja' if person.papierrechnung else 'nein'}")

        ui.label("Zugeordnete Messpunkte").classes("text-lg font-bold mt-6")
        show_all_switch = ui.switch("alle anzeigen (inkl. Historie)")
        detail_table = ui.table(columns=DETAIL_COLUMNS, rows=[], row_key="id").classes("w-full mt-2")

        def refresh_detail() -> None:
            """Reload the person's Zuordnung → Messpunkt → Standort → LEG join.

            Filters to only currently valid Zuordnungen unless
            `show_all_switch` is on.

            Returns:
                None.
            """
            today = date.today()
            with connection_scope() as inner_connection:
                zuordnungen = zuordnung_repo.list_for_person(inner_connection, person_id)
                rows = []
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
                    leg = (
                        leg_repo.get(inner_connection, standort.leg_id)
                        if standort and standort.leg_id
                        else None
                    )
                    rows.append(
                        {
                            "id": z.id,
                            "messpunkt_bezeichnung": mp.messpunkt_bezeichnung if mp else "?",
                            "messrichtung": MESSRICHTUNG_LABELS.get(mp.messrichtung, mp.messrichtung)
                            if mp
                            else "?",
                            "standort_adresse": standort.adresse_vollstaendig if standort else "?",
                            "leg": leg.name if leg else "-",
                            "gueltig_von": z.gueltig_von.isoformat(),
                            "gueltig_bis": z.gueltig_bis.isoformat() if z.gueltig_bis else "offen",
                        }
                    )
            detail_table.rows = rows
            detail_table.update()

        show_all_switch.on_value_change(lambda _: refresh_detail())
        refresh_detail()
