"""Shared site create/edit dialog."""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.quality_checks import SUBJECT_SITE
from app.gui.problem_markers import AT_THE_FIELD, load_problems, render_problem_notes
from app.gui.address_input import SuggestionBox, store_dismissals
from app.gui.form_dialog import form_guard
from app.gui.safe_notify import safe_notify
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.sort_keys import text_key
from app.models.site import Site

#: site-shaped fields `open_site_form`'s `prefill` dict may set for
#: a new site -- see that function's docstring.
_PREFILL_KEYS = ("street", "house_number", "postal_code", "municipality")


def _initial(existing: Optional[Site], attr: str, prefill: dict, key: str) -> str:
    """Resolve one field's initial form value."""
    if existing is not None:
        return getattr(existing, attr)
    return prefill.get(key, "")


def open_site_form(
    *,
    existing: Optional[Site] = None,
    prefill: Optional[dict] = None,
    on_saved: Optional[Callable[[Site], None]] = None,
) -> None:
    """Open the create/edit dialog for a site."""
    prefill = prefill or {}

    with connection_scope() as connection:
        substation_areas = substation_area_repo.list_all(connection)
    # Sorted here rather than trusting the repo's `ORDER BY name`: that is
    # SQLite's BINARY collation, which puts "TRA11600" ahead of "TRA9365"
    # and every leading umlaut behind every "Z...". A dropdown has no
    # "Sortierung" control to correct it with, so it has to arrive right.
    substation_area_options = {
        area.id: area.name for area in sorted(substation_areas, key=lambda a: text_key(a.name))
    }

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label("Standort bearbeiten" if existing else "Neuer Standort").classes("text-lg font-bold")
        # The findings for this record, except the ones rendered
        # beside their own field further down.
        if existing is not None:
            render_problem_notes(load_problems(SUBJECT_SITE).get(existing.id), exclude=AT_THE_FIELD)
        with ui.row().classes("w-full gap-2"):
            street = (
                ui.input("Adresse", value=_initial(existing, "street", prefill, "street"))
                .classes("flex-grow")
                # The cursor starts here, so the dialog can be typed into
                # without reaching for the mouse first.
                .props("autofocus")
            )
            house_number = ui.input(
                "Hausnummer", value=_initial(existing, "house_number", prefill, "house_number")
            ).classes("w-24")
        street_hint = ui.column().classes("w-full gap-0")
        with ui.row().classes("w-full gap-2"):
            postal_code = ui.input(
                "PLZ", value=_initial(existing, "postal_code", prefill, "postal_code")
            ).classes("w-24")
            municipality = ui.input(
                "Ort", value=_initial(existing, "municipality", prefill, "municipality")
            ).classes("flex-grow")
        locality_hint = ui.column().classes("w-full gap-0")
        # Suggestions and hints from the official register, if one has been
        # downloaded. A plain input with a list under it, never a select: an
        # address the register does not know still has to be typeable. The
        # hints sit under their own row, so the value a suggestion would
        # replace is visible right above it.
        suggestions = SuggestionBox(
            street,
            postal_code,
            municipality,
            house_number,
            street_hint=street_hint,
            locality_hint=locality_hint,
        )
        duplicate_warning = ui.label("").classes("text-warning")
        address_detail = ui.input(
            "Lage (optional, z. B. Stockwerk)", value=existing.address_detail if existing else ""
        ).classes("w-full")
        substation_area_select = ui.select(
            substation_area_options,
            label="Trafokreis",
            value=existing.substation_area_id if existing else None,
            with_input=True,
        ).classes("w-full")
        error_label = ui.label("").classes("text-negative")

        def check_duplicate() -> bool:
            """Check whether address/Hausnummer/PLZ already match another site."""
            if not (street.value.strip() and house_number.value.strip() and postal_code.value.strip()):
                duplicate_warning.text = ""
                return False
            with connection_scope() as connection:
                found = site_repo.find_by_address(
                    connection, street.value.strip(), house_number.value.strip(), postal_code.value.strip()
                )
            is_duplicate = found is not None and (existing is None or found.id != existing.id)
            duplicate_warning.text = (
                "Dieser Standort (Adresse, Hausnummer, PLZ) existiert bereits." if is_duplicate else ""
            )
            return is_duplicate

        street.on_value_change(lambda _: check_duplicate())
        house_number.on_value_change(lambda _: check_duplicate())
        postal_code.on_value_change(lambda _: check_duplicate())
        check_duplicate()

        def save() -> None:
            """Validate the form and persist the site."""
            if not street.value.strip():
                error_label.text = "Adresse darf nicht leer sein."
                return
            if check_duplicate():
                error_label.text = "Dieser Standort (Adresse, Hausnummer, PLZ) existiert bereits."
                return
            with connection_scope() as connection:
                if existing:
                    saved = Site(
                        id=existing.id,
                        street=street.value.strip(),
                        house_number=house_number.value.strip(),
                        postal_code=postal_code.value.strip(),
                        municipality=municipality.value.strip(),
                        address_detail=address_detail.value.strip(),
                        substation_area_id=substation_area_select.value,
                        created_at=existing.created_at,
                    )
                    site_repo.update(connection, saved)
                else:
                    saved = Site(
                        id=None,
                        street=street.value.strip(),
                        house_number=house_number.value.strip(),
                        postal_code=postal_code.value.strip(),
                        municipality=municipality.value.strip(),
                        address_detail=address_detail.value.strip(),
                        substation_area_id=substation_area_select.value,
                        created_at="",
                    )
                    new_id = site_repo.create(connection, saved)
                    saved.id = new_id
                store_dismissals(connection, suggestions, saved.id, "site")
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
    form_guard(dialog, on_save=save)
    dialog.open()
