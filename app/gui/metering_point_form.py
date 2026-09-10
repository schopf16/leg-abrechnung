"""Shared MeteringPoint create/edit dialog.

Used both by the metering points page itself and by the Web-Registrierungen
page (to prefill a new MeteringPoint from a reported Zählernummer without
having to re-type it) -- see `open_metering_point_form`'s `prefill` argument.
"""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.metering_point_validation import (
    assemble_metering_point_designation,
    validate_metering_point_designation,
)
from app.gui.safe_notify import safe_notify
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint

DIRECTION_LABELS = {
    DIRECTION_CONSUMPTION: "Bezug",
    DIRECTION_FEED_IN: "Einspeisung",
}

#: MeteringPoint-shaped fields `open_metering_point_form`'s `prefill` dict may set
#: for a new MeteringPoint -- see that function's docstring.
_PREFILL_KEYS = ("country", "identifier", "metering_point_number", "site_id", "direction")


def open_metering_point_form(
    *,
    existing: Optional[MeteringPoint] = None,
    prefill: Optional[dict] = None,
    on_saved: Optional[Callable[[MeteringPoint], None]] = None,
) -> None:
    """Open the create/edit dialog for a MeteringPoint.

    Args:
        existing: MeteringPoint to edit, or `None` to create a new one.
        prefill: Initial field values for a new MeteringPoint, ignored if
            `existing` is set. Keys: any of `_PREFILL_KEYS` (`land`,
            `identifier` default from `LegSettings` if omitted;
            `metering_point_number` defaults to ""; `site_id` defaults to
            no selection if omitted -- deliberately never guesses an
            unrelated site; `direction` defaults to
            `DIRECTION_CONSUMPTION`).
        on_saved: Called with the created/updated `MeteringPoint` right after
            a successful save (dialog already closed) -- e.g. so a caller
            elsewhere on the page can refresh its own list or react to
            the new MeteringPoint's id.

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
        default_country = existing.designation[:2]
        default_identifier = existing.designation[2:13]
        default_metering_point_number = existing.designation[13:33]
    else:
        default_country = prefill.get("country", settings.metering_point_country or "CH")
        default_identifier = prefill.get("identifier", settings.metering_point_identifier)
        default_metering_point_number = prefill.get("metering_point_number", "")

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
            country_input = ui.input("Land", value=default_country).classes("w-20")
            identifier_input = ui.input(
                "Identifikator (11-stellig)", value=default_identifier
            ).classes("flex-grow")
        metering_point_number_input = ui.input(
            "Messpunktnummer (wird rechtsbündig auf 20 Stellen mit "
            "führenden Nullen aufgefüllt)",
            value=default_metering_point_number,
        ).classes("w-full")
        designation_preview = ui.label("").classes("font-mono text-caption text-grey-8")

        def update_preview() -> None:
            """Refresh the assembled 33-character preview (for copy-paste).

            Returns:
                None.
            """
            full = assemble_metering_point_designation(
                country_input.value, identifier_input.value, metering_point_number_input.value
            )
            designation_preview.text = f"Vollständige Messpunkt-Bezeichnung: {full}"

        for field in (country_input, identifier_input, metering_point_number_input):
            field.on_value_change(lambda _: update_preview())
        update_preview()
        direction = ui.select(
            DIRECTION_LABELS,
            label="Messrichtung",
            value=existing.direction if existing else prefill.get("direction", DIRECTION_CONSUMPTION),
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
                value=existing.pv_capacity_kwp if existing else None,
                step=0.1,
            ).classes("flex-grow")
            batteriespeicher = ui.number(
                "Batteriespeicher (kWh, optional)",
                value=existing.battery_capacity_kwh if existing else None,
                step=0.1,
            ).classes("flex-grow")
        error_label = ui.label("").classes("text-negative")

        def save() -> None:
            """Validate the form and persist the MeteringPoint.

            Returns:
                None.
            """
            full_designation = assemble_metering_point_designation(
                country_input.value, identifier_input.value, metering_point_number_input.value
            )
            designation_problem = validate_metering_point_designation(full_designation)
            if designation_problem:
                error_label.text = designation_problem
                return
            if site_select.value is None:
                error_label.text = "Standort ist erforderlich."
                return
            try:
                with connection_scope() as connection:
                    if existing:
                        saved = MeteringPoint(
                            id=existing.id,
                            designation=full_designation,
                            direction=direction.value,
                            site_id=site_select.value,
                            leg_id=leg_select.value,
                            pv_capacity_kwp=pv_leistung.value,
                            battery_capacity_kwh=batteriespeicher.value,
                            created_at=existing.created_at,
                        )
                        metering_point_repo.update(connection, saved)
                    else:
                        saved = MeteringPoint(
                            id=None,
                            designation=full_designation,
                            direction=direction.value,
                            site_id=site_select.value,
                            leg_id=leg_select.value,
                            pv_capacity_kwp=pv_leistung.value,
                            battery_capacity_kwh=batteriespeicher.value,
                            created_at="",
                        )
                        new_id = metering_point_repo.create(connection, saved)
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
