"""Shared Messpunkt create/edit dialog.

Used both by the Messpunkte page itself and by the Web-Registrierungen
page (to prefill a new Messpunkt from a reported Zählernummer without
having to re-type it) -- see `open_messpunkt_form`'s `prefill` argument.
"""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.messpunkt_validation import (
    assemble_messpunkt_bezeichnung,
    validate_messpunkt_bezeichnung,
)
from app.gui.safe_notify import safe_notify
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models.messpunkt import MESSRICHTUNG_BEZUG, MESSRICHTUNG_EINSPEISUNG, Messpunkt

MESSRICHTUNG_LABELS = {
    MESSRICHTUNG_BEZUG: "Bezug",
    MESSRICHTUNG_EINSPEISUNG: "Einspeisung",
}

#: Messpunkt-shaped fields `open_messpunkt_form`'s `prefill` dict may set
#: for a new Messpunkt -- see that function's docstring.
_PREFILL_KEYS = ("land", "identifikator", "messpunktnummer", "site_id", "messrichtung")


def open_messpunkt_form(
    *,
    existing: Optional[Messpunkt] = None,
    prefill: Optional[dict] = None,
    on_saved: Optional[Callable[[Messpunkt], None]] = None,
) -> None:
    """Open the create/edit dialog for a Messpunkt.

    Args:
        existing: Messpunkt to edit, or `None` to create a new one.
        prefill: Initial field values for a new Messpunkt, ignored if
            `existing` is set. Keys: any of `_PREFILL_KEYS` (`land`,
            `identifikator` default from `LegSettings` if omitted;
            `messpunktnummer` defaults to ""; `site_id` defaults to
            no selection if omitted -- deliberately never guesses an
            unrelated site; `messrichtung` defaults to
            `MESSRICHTUNG_BEZUG`).
        on_saved: Called with the created/updated `Messpunkt` right after
            a successful save (dialog already closed) -- e.g. so a caller
            elsewhere on the page can refresh its own list or react to
            the new Messpunkt's id.

    Returns:
        None.
    """
    prefill = prefill or {}

    with connection_scope() as connection:
        sites = site_repo.list_all(connection)
        legs = leg_repo.list_all(connection)
        settings = settings_repo.get_settings(connection)
    site_options = {s.id: s.full_address for s in sites}
    leg_options = {leg.id: leg.name for leg in legs}

    if existing:
        default_land = existing.messpunkt_bezeichnung[:2]
        default_identifikator = existing.messpunkt_bezeichnung[2:13]
        default_messpunktnummer = existing.messpunkt_bezeichnung[13:33]
    else:
        default_land = prefill.get("land", settings.messpunkt_land or "CH")
        default_identifikator = prefill.get("identifikator", settings.messpunkt_identifikator)
        default_messpunktnummer = prefill.get("messpunktnummer", "")

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label("Messpunkt bearbeiten" if existing else "Neuer Messpunkt").classes(
            "text-lg font-bold"
        )
        ui.label(
            "Messpunkt-Bezeichnung: Land + Identifikator sind bei "
            "allen Messpunkten dieser LEG gleich (Vorgabe aus den "
            "Einstellungen, hier veränderbar) -- nur die "
            "Messpunktnummer unterscheidet sich je Zähler."
        ).classes("text-caption text-grey-6")
        with ui.row().classes("w-full gap-2"):
            land_input = ui.input("Land", value=default_land).classes("w-20")
            identifikator_input = ui.input(
                "Identifikator (11-stellig)", value=default_identifikator
            ).classes("flex-grow")
        messpunktnummer_input = ui.input(
            "Messpunktnummer (wird rechtsbündig auf 20 Stellen mit "
            "führenden Nullen aufgefüllt)",
            value=default_messpunktnummer,
        ).classes("w-full")
        bezeichnung_preview = ui.label("").classes("font-mono text-caption text-grey-8")

        def update_preview() -> None:
            """Refresh the assembled 33-character preview (for copy-paste).

            Returns:
                None.
            """
            full = assemble_messpunkt_bezeichnung(
                land_input.value, identifikator_input.value, messpunktnummer_input.value
            )
            bezeichnung_preview.text = f"Vollständige Messpunkt-Bezeichnung: {full}"

        for field in (land_input, identifikator_input, messpunktnummer_input):
            field.on_value_change(lambda _: update_preview())
        update_preview()
        messrichtung = ui.select(
            MESSRICHTUNG_LABELS,
            label="Messrichtung",
            value=existing.messrichtung if existing else prefill.get("messrichtung", MESSRICHTUNG_BEZUG),
        ).classes("w-full")
        site_select = ui.select(
            site_options,
            label="Standort",
            value=existing.site_id if existing else prefill.get("site_id"),
        ).classes("w-full")
        leg_select = ui.select(
            leg_options,
            label="LEG",
            value=existing.leg_id if existing else None,
            with_input=True,
        ).classes("w-full")
        with ui.row().classes("w-full gap-2"):
            pv_leistung = ui.number(
                "PV-Leistung (kWp, optional)",
                value=existing.pv_leistung_kwp if existing else None,
                step=0.1,
            ).classes("flex-grow")
            batteriespeicher = ui.number(
                "Batteriespeicher (kWh, optional)",
                value=existing.batteriespeicher_kwh if existing else None,
                step=0.1,
            ).classes("flex-grow")
        error_label = ui.label("").classes("text-negative")

        def save() -> None:
            """Validate the form and persist the Messpunkt.

            Returns:
                None.
            """
            full_bezeichnung = assemble_messpunkt_bezeichnung(
                land_input.value, identifikator_input.value, messpunktnummer_input.value
            )
            bezeichnung_problem = validate_messpunkt_bezeichnung(full_bezeichnung)
            if bezeichnung_problem:
                error_label.text = bezeichnung_problem
                return
            if site_select.value is None:
                error_label.text = "Standort ist erforderlich."
                return
            try:
                with connection_scope() as connection:
                    if existing:
                        saved = Messpunkt(
                            id=existing.id,
                            messpunkt_bezeichnung=full_bezeichnung,
                            messrichtung=messrichtung.value,
                            site_id=site_select.value,
                            leg_id=leg_select.value,
                            pv_leistung_kwp=pv_leistung.value,
                            batteriespeicher_kwh=batteriespeicher.value,
                            created_at=existing.created_at,
                        )
                        messpunkt_repo.update(connection, saved)
                    else:
                        saved = Messpunkt(
                            id=None,
                            messpunkt_bezeichnung=full_bezeichnung,
                            messrichtung=messrichtung.value,
                            site_id=site_select.value,
                            leg_id=leg_select.value,
                            pv_leistung_kwp=pv_leistung.value,
                            batteriespeicher_kwh=batteriespeicher.value,
                            created_at="",
                        )
                        new_id = messpunkt_repo.create(connection, saved)
                        saved.id = new_id
            except Exception as exc:  # unique constraint, etc.
                error_label.text = f"Fehler beim Speichern: {exc}"
                return
            dialog.close()
            # Notify before any caller-side refresh() -- see
            # app.gui.safe_notify for why (a caller may tear down this
            # dialog's own UI context inside on_saved()).
            safe_notify("Gespeichert.", type="positive")
            if on_saved:
                on_saved(saved)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            ui.button("Speichern", on_click=save)
    dialog.open()
