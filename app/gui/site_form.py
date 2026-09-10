"""Shared site create/edit dialog.

Used both by the sites page itself and by the Web-Registrierungen page
(to prefill a new site from a registration's reported address without
having to re-type it) -- see `open_site_form`'s `prefill` argument.
"""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.safe_notify import safe_notify
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.site import Site

#: site-shaped fields `open_site_form`'s `prefill` dict may set for
#: a new site -- see that function's docstring.
_PREFILL_KEYS = ("street", "house_number", "postal_code", "municipality")


def _initial(existing: Optional[Site], attr: str, prefill: dict, key: str) -> str:
    """Resolve one field's initial form value.

    Args:
        existing: site being edited, or `None` when creating.
        attr: Attribute name on `existing` to read when editing.
        prefill: Prefill dict passed to `open_site_form`.
        key: Key to look up in `prefill` when creating.

    Returns:
        The value the corresponding input should start with.
    """
    if existing is not None:
        return getattr(existing, attr)
    return prefill.get(key, "")


def open_site_form(
    *,
    existing: Optional[Site] = None,
    prefill: Optional[dict] = None,
    on_saved: Optional[Callable[[Site], None]] = None,
) -> None:
    """Open the create/edit dialog for a site.

    Args:
        existing: site to edit, or `None` to create a new one.
        prefill: Initial field values for a new site, ignored if
            `existing` is set. Keys: any of `_PREFILL_KEYS` (`street`,
            `house_number`, `postal_code`, `municipality`); missing keys default to "".
        on_saved: Called with the created/updated `site` right after a
            successful save (dialog already closed) -- e.g. so a caller
            elsewhere on the page can refresh its own list or react to
            the new site's id.

    Returns:
        None.
    """
    prefill = prefill or {}

    with connection_scope() as connection:
        substation_areas = substation_area_repo.list_all(connection)
    substation_area_options = {t.id: t.name for t in substation_areas}

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label("Standort bearbeiten" if existing else "Neuer Standort").classes(
            "text-lg font-bold"
        )
        with ui.row().classes("w-full gap-2"):
            street = ui.input(
                "Adresse", value=_initial(existing, "street", prefill, "street")
            ).classes("flex-grow").props("debounce=300")
            house_number = ui.input(
                "Hausnummer", value=_initial(existing, "house_number", prefill, "house_number")
            ).classes("w-24").props("debounce=300")
        with ui.row().classes("w-full gap-2"):
            postal_code = ui.input(
                "PLZ", value=_initial(existing, "postal_code", prefill, "postal_code")
            ).classes("w-24").props("debounce=300")
            municipality = ui.input(
                "Gemeinde", value=_initial(existing, "municipality", prefill, "municipality")
            ).classes("flex-grow")
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
            """Check whether address/Hausnummer/PLZ already match another site.

            Updates `duplicate_warning` as a side effect.

            Returns:
                `True` if a different site already has this exact address.
            """
            if not (street.value.strip() and house_number.value.strip() and postal_code.value.strip()):
                duplicate_warning.text = ""
                return False
            with connection_scope() as connection:
                found = site_repo.find_by_address(
                    connection, street.value.strip(), house_number.value.strip(), postal_code.value.strip()
                )
            is_duplicate = found is not None and (existing is None or found.id != existing.id)
            duplicate_warning.text = (
                "Dieser Standort (Adresse, Hausnummer, PLZ) existiert bereits."
                if is_duplicate
                else ""
            )
            return is_duplicate

        street.on_value_change(lambda _: check_duplicate())
        house_number.on_value_change(lambda _: check_duplicate())
        postal_code.on_value_change(lambda _: check_duplicate())
        check_duplicate()

        def save() -> None:
            """Validate the form and persist the site.

            Returns:
                None.
            """
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
