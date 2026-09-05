"""Shared Standort create/edit dialog.

Used both by the Standorte page itself and by the Web-Registrierungen page
(to prefill a new Standort from a registration's reported address without
having to re-type it) -- see `open_standort_form`'s `prefill` argument.
"""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.safe_notify import safe_notify
from app.models import standort as standort_repo
from app.models import trafokreis as trafokreis_repo
from app.models.standort import Standort

#: Standort-shaped fields `open_standort_form`'s `prefill` dict may set for
#: a new Standort -- see that function's docstring.
_PREFILL_KEYS = ("adresse", "hausnummer", "plz", "gemeinde")


def _initial(existing: Optional[Standort], attr: str, prefill: dict, key: str) -> str:
    """Resolve one field's initial form value.

    Args:
        existing: Standort being edited, or `None` when creating.
        attr: Attribute name on `existing` to read when editing.
        prefill: Prefill dict passed to `open_standort_form`.
        key: Key to look up in `prefill` when creating.

    Returns:
        The value the corresponding input should start with.
    """
    if existing is not None:
        return getattr(existing, attr)
    return prefill.get(key, "")


def open_standort_form(
    *,
    existing: Optional[Standort] = None,
    prefill: Optional[dict] = None,
    on_saved: Optional[Callable[[Standort], None]] = None,
) -> None:
    """Open the create/edit dialog for a Standort.

    Args:
        existing: Standort to edit, or `None` to create a new one.
        prefill: Initial field values for a new Standort, ignored if
            `existing` is set. Keys: any of `_PREFILL_KEYS` (`adresse`,
            `hausnummer`, `plz`, `gemeinde`); missing keys default to "".
        on_saved: Called with the created/updated `Standort` right after a
            successful save (dialog already closed) -- e.g. so a caller
            elsewhere on the page can refresh its own list or react to
            the new Standort's id.

    Returns:
        None.
    """
    prefill = prefill or {}

    with connection_scope() as connection:
        trafokreise = trafokreis_repo.list_all(connection)
    trafokreis_options = {t.id: t.name for t in trafokreise}

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label("Standort bearbeiten" if existing else "Neuer Standort").classes(
            "text-lg font-bold"
        )
        with ui.row().classes("w-full gap-2"):
            adresse = ui.input(
                "Adresse", value=_initial(existing, "adresse", prefill, "adresse")
            ).classes("flex-grow").props("debounce=300")
            hausnummer = ui.input(
                "Hausnummer", value=_initial(existing, "hausnummer", prefill, "hausnummer")
            ).classes("w-24").props("debounce=300")
        with ui.row().classes("w-full gap-2"):
            plz = ui.input(
                "PLZ", value=_initial(existing, "plz", prefill, "plz")
            ).classes("w-24").props("debounce=300")
            gemeinde = ui.input(
                "Gemeinde", value=_initial(existing, "gemeinde", prefill, "gemeinde")
            ).classes("flex-grow")
        duplicate_warning = ui.label("").classes("text-warning")
        lage = ui.input(
            "Lage (optional, z. B. Stockwerk)", value=existing.lage if existing else ""
        ).classes("w-full")
        trafokreis_select = ui.select(
            trafokreis_options,
            label="Trafokreis",
            value=existing.trafokreis_id if existing else None,
            with_input=True,
        ).classes("w-full")
        error_label = ui.label("").classes("text-negative")

        def check_duplicate() -> bool:
            """Check whether Adresse/Hausnummer/PLZ already match another Standort.

            Updates `duplicate_warning` as a side effect.

            Returns:
                `True` if a different Standort already has this exact address.
            """
            if not (adresse.value.strip() and hausnummer.value.strip() and plz.value.strip()):
                duplicate_warning.text = ""
                return False
            with connection_scope() as connection:
                found = standort_repo.find_by_address(
                    connection, adresse.value.strip(), hausnummer.value.strip(), plz.value.strip()
                )
            is_duplicate = found is not None and (existing is None or found.id != existing.id)
            duplicate_warning.text = (
                "Dieser Standort (Adresse, Hausnummer, PLZ) existiert bereits."
                if is_duplicate
                else ""
            )
            return is_duplicate

        adresse.on_value_change(lambda _: check_duplicate())
        hausnummer.on_value_change(lambda _: check_duplicate())
        plz.on_value_change(lambda _: check_duplicate())
        check_duplicate()

        def save() -> None:
            """Validate the form and persist the Standort.

            Returns:
                None.
            """
            if not adresse.value.strip():
                error_label.text = "Adresse darf nicht leer sein."
                return
            if check_duplicate():
                error_label.text = "Dieser Standort (Adresse, Hausnummer, PLZ) existiert bereits."
                return
            with connection_scope() as connection:
                if existing:
                    saved = Standort(
                        id=existing.id,
                        adresse=adresse.value.strip(),
                        hausnummer=hausnummer.value.strip(),
                        plz=plz.value.strip(),
                        gemeinde=gemeinde.value.strip(),
                        lage=lage.value.strip(),
                        trafokreis_id=trafokreis_select.value,
                        created_at=existing.created_at,
                    )
                    standort_repo.update(connection, saved)
                else:
                    saved = Standort(
                        id=None,
                        adresse=adresse.value.strip(),
                        hausnummer=hausnummer.value.strip(),
                        plz=plz.value.strip(),
                        gemeinde=gemeinde.value.strip(),
                        lage=lage.value.strip(),
                        trafokreis_id=trafokreis_select.value,
                        created_at="",
                    )
                    new_id = standort_repo.create(connection, saved)
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
