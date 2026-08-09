"""Messpunkt-to-Person assignment history page (Zuordnungen).

Rendered as one card per Messpunkt (grouping its Zuordnungen together)
rather than a flat table: edit/delete buttons are bound directly to Python
callbacks (not via a JS-emit round trip through a Quasar table slot),
which is both more robust to click on and groups related entries more
usefully than one row per Zuordnung in isolation.
"""

from datetime import date, datetime
from typing import Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.leg_composition import compute_leg_composition
from app.gui.navigation import page_frame
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import standort as standort_repo
from app.models import zuordnung as zuordnung_repo
from app.models.zuordnung import Zuordnung


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


@ui.page("/zuordnungen")
def zuordnungen_page() -> None:
    """Render the Zuordnungen CRUD page, including consistency warnings.

    Returns:
        None.
    """
    with page_frame("/zuordnungen", "Zuordnungen"):
        ui.label(
            "Legt fest, welcher Person ein Messpunkt in welchem Zeitraum "
            "zugeordnet ist. Bei einem Umzug mitten im Quartal zwei "
            "Zuordnungen mit passendem Enddatum/Startdatum anlegen."
        ).classes("text-body2 text-grey-8")

        warnings_column = ui.column().classes("w-full")
        list_container = ui.column().classes("w-full gap-2 mt-2")

        def render_group(messpunkt_label: str, group: list[dict]) -> None:
            """Render one Messpunkt's card with all of its Zuordnungen.

            Args:
                messpunkt_label: Display label for the Messpunkt heading.
                group: Row dicts (see `refresh`) belonging to that Messpunkt,
                    already sorted by `gueltig_von`.

            Returns:
                None.
            """
            with ui.card().classes("w-full"):
                ui.label(messpunkt_label).classes("font-bold")
                for row in group:
                    with ui.row().classes("w-full items-center gap-4 flex-wrap"):
                        ui.label(row["person_name"]).classes("min-w-[180px]")
                        ui.label(f"ab {row['gueltig_von']}").classes("min-w-[120px] text-grey-7")
                        ui.label(f"bis {row['gueltig_bis']}").classes("min-w-[120px] text-grey-7")
                        with ui.row().classes("gap-1 ml-auto"):
                            ui.button(
                                icon="edit", on_click=lambda z=row["zuordnung"]: on_edit(z)
                            ).props("dense flat")
                            ui.button(
                                icon="delete",
                                on_click=lambda z=row["zuordnung"]: on_remove(z),
                            ).props("dense flat color=negative")

        def refresh() -> None:
            """Reload the Zuordnungen list (grouped by Messpunkt) and
            recompute consistency warnings.

            Returns:
                None.
            """
            with connection_scope() as connection:
                messpunkte = {mp.id: mp for mp in messpunkt_repo.list_all(connection)}
                standorte = {s.id: s for s in standort_repo.list_all(connection)}
                persons = {p.id: p for p in person_repo.list_all(connection)}
                zuordnungen = zuordnung_repo.list_all(connection)
                all_warnings = []
                for messpunkt_id in messpunkte:
                    all_warnings.extend(zuordnung_repo.find_warnings(connection, messpunkt_id))

            groups: dict[int, list[dict]] = {}
            for z in zuordnungen:
                groups.setdefault(z.messpunkt_id, []).append(
                    {
                        "zuordnung": z,
                        "person_name": persons[z.person_id].anzeige_name
                        if z.person_id in persons
                        else "?",
                        "gueltig_von": z.gueltig_von.isoformat(),
                        "gueltig_bis": z.gueltig_bis.isoformat() if z.gueltig_bis else "offen",
                    }
                )

            list_container.clear()
            with list_container:
                if not groups:
                    ui.label("Noch keine Zuordnungen erfasst.")
                for messpunkt_id, group in groups.items():
                    mp = messpunkte.get(messpunkt_id)
                    if mp is None:
                        label = f"Messpunkt #{messpunkt_id}"
                    else:
                        standort = standorte.get(mp.standort_id)
                        standort_text = standort.adresse_vollstaendig if standort else "?"
                        label = f"{mp.messpunkt_bezeichnung} — {standort_text}"
                    render_group(label, group)

            warnings_column.clear()
            with warnings_column:
                for warning in all_warnings:
                    ui.label(f"⚠ {warning.message}").classes(
                        "text-negative text-body2"
                    )

        def open_form(existing: Optional[Zuordnung]) -> None:
            """Open the create/edit dialog for a Zuordnung.

            Args:
                existing: Zuordnung to edit, or `None` to create a new one.

            Returns:
                None.
            """
            with connection_scope() as connection:
                messpunkte = messpunkt_repo.list_all(connection)
                standorte = standort_repo.list_all(connection)
                persons = person_repo.list_all(connection)
            messpunkte_by_id = {mp.id: mp for mp in messpunkte}
            standort_options = {s.id: s.adresse_vollstaendig for s in standorte}

            def messpunkt_options_for(standort_id: Optional[int]) -> dict:
                """Build the Messpunkt dropdown options, optionally filtered by Standort.

                Args:
                    standort_id: If set, only Messpunkte at that Standort
                        are included; `None` includes all of them.

                Returns:
                    A `{messpunkt_id: label}` dict for `ui.select`.
                """
                return {
                    mp.id: f"{mp.messpunkt_bezeichnung} ({'Bezug' if mp.is_bezug else 'Einspeisung'})"
                    for mp in messpunkte
                    if standort_id is None or mp.standort_id == standort_id
                }

            # Deactivated persons are hidden from selection for new Zuordnungen,
            # but stay selectable when editing a Zuordnung that already points
            # at one (see app.models.person.delete).
            selectable_persons = [
                p for p in persons if p.aktiv or (existing and p.id == existing.person_id)
            ]
            person_options = {
                p.id: p.anzeige_name + ("" if p.aktiv else " (inaktiv)") for p in selectable_persons
            }

            initial_standort_id = (
                messpunkte_by_id[existing.messpunkt_id].standort_id
                if existing and existing.messpunkt_id in messpunkte_by_id
                else None
            )

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label(
                    "Zuordnung bearbeiten" if existing else "Neue Zuordnung"
                ).classes("text-lg font-bold")
                standort_select = ui.select(
                    {None: "Alle Standorte", **standort_options},
                    label="Standort (Filter für Messpunkt)",
                    value=initial_standort_id,
                    with_input=True,
                ).classes("w-full")
                messpunkt_select = ui.select(
                    messpunkt_options_for(initial_standort_id),
                    label="Messpunkt",
                    value=existing.messpunkt_id if existing else None,
                    with_input=True,
                ).classes("w-full")
                leg_warning = ui.label("").classes("text-warning text-body2")

                def on_standort_change() -> None:
                    """Re-filter the Messpunkt options to the selected Standort.

                    Returns:
                        None.
                    """
                    options = messpunkt_options_for(standort_select.value)
                    messpunkt_select.options = options
                    if messpunkt_select.value not in options:
                        messpunkt_select.value = next(iter(options), None)
                    messpunkt_select.update()
                    update_leg_warning()

                standort_select.on_value_change(lambda _: on_standort_change())

                def update_leg_warning() -> None:
                    """Show a warning if the selected Messpunkt's LEG mixes Trafokreise.

                    Lets the administrator immediately see, while assigning
                    a Person, whether the resulting LEG membership implies
                    a reduced BKW discount -- see `app.domain.leg_composition`.

                    Returns:
                        None.
                    """
                    mp = messpunkte_by_id.get(messpunkt_select.value)
                    if mp is None or mp.leg_id is None:
                        leg_warning.text = ""
                        return
                    with connection_scope() as connection:
                        composition = compute_leg_composition(connection, mp.leg_id)
                        leg = leg_repo.get(connection, mp.leg_id)
                    if composition.is_mixed and leg is not None:
                        trafokreis_names = ", ".join(t.name for t in composition.trafokreise)
                        leg_warning.text = (
                            f"⚠ Die LEG „{leg.name}“ dieses Messpunkts umfasst "
                            f"mehrere Trafokreise ({trafokreis_names}) -- "
                            "informieren Sie die Person ggf. über den "
                            "dadurch tieferen BKW-Rabatt."
                        )
                    else:
                        leg_warning.text = ""

                messpunkt_select.on_value_change(lambda _: update_leg_warning())
                update_leg_warning()
                person_select = ui.select(
                    person_options,
                    label="Person",
                    value=existing.person_id
                    if existing
                    else (selectable_persons[0].id if selectable_persons else None),
                ).classes("w-full")
                gueltig_von = ui.input(
                    "Gültig von",
                    value=existing.gueltig_von.isoformat() if existing else date.today().isoformat(),
                ).props("type=date").classes("w-full")
                gueltig_bis = ui.input(
                    "Gültig bis (leer = offen)",
                    value=existing.gueltig_bis.isoformat() if existing and existing.gueltig_bis else "",
                ).props("type=date").classes("w-full")
                error_label = ui.label("").classes("text-negative")

                def save() -> None:
                    """Validate the form and persist the Zuordnung.

                    Returns:
                        None.
                    """
                    if messpunkt_select.value is None or person_select.value is None:
                        error_label.text = "Messpunkt und Person sind erforderlich."
                        return
                    try:
                        from_date = _parse_date(gueltig_von.value)
                        to_date = _parse_date(gueltig_bis.value)
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
                            updated = Zuordnung(
                                id=existing.id,
                                person_id=person_select.value,
                                messpunkt_id=messpunkt_select.value,
                                gueltig_von=from_date,
                                gueltig_bis=to_date,
                                created_at=existing.created_at,
                            )
                            zuordnung_repo.update(connection, updated)
                        else:
                            new_zuordnung = Zuordnung(
                                id=None,
                                person_id=person_select.value,
                                messpunkt_id=messpunkt_select.value,
                                gueltig_von=from_date,
                                gueltig_bis=to_date,
                                created_at="",
                            )
                            zuordnung_repo.create(connection, new_zuordnung)
                    dialog.close()
                    refresh()
                    ui.notify("Gespeichert.", type="positive")

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    ui.button("Speichern", on_click=save)
            dialog.open()

        def on_edit(zuordnung: Zuordnung) -> None:
            """Card edit-button handler: open the edit dialog for this Zuordnung.

            Args:
                zuordnung: Zuordnung to edit.

            Returns:
                None.
            """
            open_form(zuordnung)

        def on_remove(zuordnung: Zuordnung) -> None:
            """Card delete-button handler: delete the Zuordnung after confirmation.

            Args:
                zuordnung: Zuordnung to delete.

            Returns:
                None.
            """
            with ui.dialog() as confirm, ui.card():
                ui.label("Diese Zuordnung wirklich löschen?")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    def do_delete() -> None:
                        with connection_scope() as connection:
                            zuordnung_repo.delete(connection, zuordnung.id)
                        confirm.close()
                        refresh()
                        ui.notify("Gelöscht.", type="warning")

                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        ui.button("+ Neue Zuordnung", on_click=lambda: open_form(None)).classes("mt-2")

        refresh()
