"""The LEG create/edit dialog, in a module of its own.

Same shape and the same reason as `app.gui.site_form`,
`app.gui.person_form` and `app.gui.metering_point_form`: a dialog nested
inside a list page can only be opened from that page, so the LEG detail page
had no Bearbeiten button -- the eye led somewhere the pencil could not
follow.

Nothing about the dialog itself changed in the move. The one difference is
that it reports a successful save through `on_saved` instead of closing over
the list page's `refresh`.
"""

from datetime import date
from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.form_dialog import form_guard
from app.gui.safe_notify import safe_notify
from app.models import leg as leg_repo
from app.models.leg import Leg


def open_leg_form(
    *,
    existing: Optional[Leg] = None,
    on_saved: Optional[Callable[[Optional[Leg]], None]] = None,
) -> None:
    """Open the create/edit dialog for a LEG.

    Args:
        existing: LEG to edit, or `None` to create a new one.
        on_saved: Called after a successful save, with the saved LEG for an
            edit and `None` for a new one -- the same shape
            `app.gui.site_form` uses, so a caller can refresh whatever it
            needs to.

    Returns:
        None.
    """
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label("LEG bearbeiten" if existing else "Neue LEG").classes("text-lg font-bold")
        name = (
            ui.input(
                "Name (Trafokreis-Bezeichnung oder eigener LEG-Name)",
                value=existing.name if existing else "",
            )
            .classes("w-full")
            .props("debounce=300")
        )
        duplicate_warning = ui.label("").classes("text-warning")
        # No upper bound: Art. 19e sets only a minimum, and a LEG
        # with a large producer and few consumers legitimately shows
        # over 100% in the portal. `ui.number`'s max clamps silently
        # on blur, so setting one would quietly corrupt such a value.
        capacity_percent = ui.number(
            "Produktionsleistung (% der Anschlussleistung)",
            value=existing.production_capacity_percent if existing else None,
            min=0,
            step=0.1,
        ).classes("w-full")
        capacity_date = (
            ui.input(
                "Stand vom",
                value=(existing.production_capacity_recorded_at if existing else "")
                or date.today().isoformat(),
            )
            .props("type=date")
            .classes("w-full")
        )

        def _stamp_today() -> None:
            """Move the Stand to today whenever the percentage changes.

            A fresh figure carried an old date otherwise, and the
            date is the only staleness safeguard the feature has.
            Still editable afterwards, for entering an older
            reading on purpose.

            Returns:
                None.
            """
            previous = existing.production_capacity_percent if existing else None
            if capacity_percent.value != previous:
                capacity_date.value = date.today().isoformat()

        capacity_percent.on_value_change(lambda _: _stamp_today())

        ui.label(
            "Wert aus dem BKW-LEG-Portal, das ihn bei jeder Messpunkt-Anmeldung "
            "anzeigt („37.6 % tatsächlich / 5 % erforderlich“). Mindestens 5 % "
            "sind gesetzlich nötig (Art. 19e Abs. 1 StromVV). Die App kann den "
            "Wert nicht selbst berechnen -- die Anschlussleistung der Standorte "
            "ist ihr nicht bekannt."
        ).classes("text-caption text-grey-6")
        note = (
            ui.textarea(
                "Bemerkung (optional)",
                value=existing.note if existing else "",
            )
            .classes("w-full")
            .props("rows=3")
        )
        error_label = ui.label("").classes("text-negative")

        def check_duplicate() -> bool:
            """Check whether the current name input is already used by another LEG.

            Updates `duplicate_warning` as a side effect.

            Returns:
                `True` if the name is a duplicate of a different LEG.
            """
            typed = name.value.strip()
            if not typed:
                duplicate_warning.text = ""
                return False
            with connection_scope() as connection:
                found = leg_repo.get_by_name(connection, typed)
            is_duplicate = found is not None and (existing is None or found.id != existing.id)
            duplicate_warning.text = "Dieser Name wird bereits verwendet." if is_duplicate else ""
            return is_duplicate

        name.on_value_change(lambda _: check_duplicate())

        def save() -> None:
            """Validate the form and persist the LEG.

            Returns:
                None.
            """
            if not name.value.strip():
                error_label.text = "Name darf nicht leer sein."
                return
            if check_duplicate():
                error_label.text = "Dieser Name wird bereits verwendet."
                return
            if capacity_percent.value is not None:
                if capacity_percent.value < 0:
                    error_label.text = "Produktionsleistung darf nicht negativ sein."
                    return
                if not capacity_date.value:
                    error_label.text = "Bitte das Datum angeben, an dem der Wert im BKW-Portal gelesen wurde."
                    return
            try:
                with connection_scope() as connection:
                    if existing:
                        updated = Leg(
                            id=existing.id,
                            name=name.value.strip(),
                            note=note.value.strip(),
                            created_at=existing.created_at,
                            production_capacity_percent=capacity_percent.value,
                            production_capacity_recorded_at=(
                                (capacity_date.value or None) if capacity_percent.value is not None else None
                            ),
                        )
                        leg_repo.update(connection, updated)
                    else:
                        new_leg = Leg(
                            id=None,
                            name=name.value.strip(),
                            note=note.value.strip(),
                            created_at="",
                            production_capacity_percent=capacity_percent.value,
                            production_capacity_recorded_at=(
                                (capacity_date.value or None) if capacity_percent.value is not None else None
                            ),
                        )
                        leg_repo.create(connection, new_leg)
            except Exception as exc:  # unique constraint race, etc.
                error_label.text = f"Fehler beim Speichern: {exc}"
                return
            dialog.close()
            # notify before the callback -- see app.gui.safe_notify's
            # module docstring for why a plain ui.notify() here can
            # raise "parent element ... has been deleted" once the
            # card this dialog was opened from is gone.
            safe_notify("Gespeichert.", type="positive")
            if on_saved:
                on_saved(existing)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            ui.button("Speichern", on_click=save)
    form_guard(dialog, on_save=save)
    dialog.open()
