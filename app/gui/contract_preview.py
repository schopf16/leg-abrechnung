"""Look at a filled-in Beitrittserklärung before anybody sends one."""

import os
from pathlib import Path
from typing import Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.membership_contract import gather
from app.domain.message_attachments import safe_filename
from app.gui.safe_notify import safe_notify
from app.models import person as person_repo
from app.paths import OUTPUT_DIR
from app.pdf.membership_contract import build_contract
from app.sort_keys import person_name_key


def open_contract_preview(*, document_key: str) -> None:
    """Ask which person, build their Beitrittserklärung, and say where it is."""
    with connection_scope() as connection:
        people = sorted(
            (person for person in person_repo.list_all(connection) if person.active),
            key=person_name_key,
        )

    options = {person.id: person.display_name for person in people}

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
        ui.label("Beitrittserklärung ansehen").classes("text-lg font-bold")
        ui.label(
            "Erzeugt die mit der Software gelieferte Beitrittserklärung für eine Person, "
            "ausgefüllt mit den gespeicherten Angaben. Es wird nichts "
            "versendet."
        ).classes("text-body2 text-grey-8")

        person_select = ui.select(
            options, label="Person", with_input=True, value=people[0].id if people else None
        ).classes("w-full")
        result = ui.column().classes("w-full gap-1")

        def build() -> None:
            """Write the document and show where it landed."""
            if person_select.value is None:
                safe_notify("Bitte eine Person wählen.", type="warning")
                return
            with connection_scope() as connection:
                person = person_repo.get(connection, person_select.value)
                if person is None:
                    safe_notify("Diese Person gibt es nicht mehr.", type="warning")
                    return
                fields = gather(connection, person)

            target = OUTPUT_DIR / "Vorschau" / f"Beitrittserklaerung_{safe_filename(person.display_name)}.pdf"
            try:
                build_contract(fields, None, target)
            except ValueError as exc:
                safe_notify(f"Beitrittserklärung konnte nicht erzeugt werden: {exc}", type="negative")
                return

            result.clear()
            with result:
                ui.label("Erzeugt:").classes("font-bold")
                # The path in full: every other document in this app is
                # surfaced the same way (see `app.pdf.export_service`), and
                # the administrator works on the machine the file is on.
                ui.label(str(target)).classes("text-caption text-grey-7 whitespace-pre-wrap")
                _render_missing_note(fields)
                ui.button("PDF öffnen", on_click=lambda: _open(target)).props("flat color=primary")

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Schliessen", on_click=dialog.close).props("flat")
            ui.button("Erzeugen", on_click=build)
    dialog.open()


def _render_missing_note(fields) -> None:
    """Name the fields that came out blank, so a gap is not mistaken for a bug."""
    labels = {
        "names": "Vorname, Name",
        "address": "Adresse",
        "locality": "PLZ / Ort",
        "email": "E-Mail",
        "phone": "Tel",
        "substation_area": "Trafokreis",
    }
    empty = [label for name, label in labels.items() if not getattr(fields, name)]
    if not fields.consumption_designations and not fields.feed_in_designations:
        empty.append("Messpunktnummer")
    if not empty:
        return
    ui.label("Leer geblieben: " + ", ".join(empty)).classes("text-caption text-warning")


def _open(path: Path) -> None:
    """Hand one file to the system viewer."""
    opener: Optional[object] = getattr(os, "startfile", None)
    if opener is None:
        safe_notify(f"Datei liegt unter: {path}", type="info")
        return
    try:
        opener(str(path))  # type: ignore[operator]
    except OSError as exc:
        safe_notify(f"Konnte die Datei nicht öffnen: {exc}", type="negative")
