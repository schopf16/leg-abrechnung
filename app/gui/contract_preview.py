"""Look at a filled-in Beitrittserklärung before anybody sends one.

The administrator's question, and it is the right one: *"wie kann ich prüfen
dass die erste seite korrekt ausgefüllt wird?"* Page 1 is drawn from twelve
stored values and one hand-entered one, and no test can answer whether the
result reads correctly to a human. So this builds the document for a person
of their choosing and says where it is.

Deliberately available from the Textbausteine page, beside the checkbox that
attaches the contract: the question arises exactly while ticking that box.
It is also what the send dialog will show later (stage 3) -- the same
generator, so the preview cannot drift from what goes out.

**It writes into `output/` and names the path**, which is what every other
document in this app does (`app.pdf.export_service`). The one addition is a
button that hands the file to the system viewer, because a preview nobody
opens is not a preview. Windows-only, like the app.
"""

import os
from pathlib import Path
from typing import Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.membership_contract import gather
from app.gui.safe_notify import safe_notify
from app.models import leg_document as leg_document_repo
from app.models import person as person_repo
from app.paths import OUTPUT_DIR
from app.pdf.membership_contract import build_contract
from app.sort_keys import person_name_key


def _safe_filename(text: str) -> str:
    """Reduce a name to something Windows accepts in a filename.

    Args:
        text: The person's display name.

    Returns:
        The name with path-unsafe characters replaced.
    """
    keep = [character if character.isalnum() or character in " -_" else "_" for character in text]
    return "".join(keep).strip() or "Person"


def open_contract_preview(*, document_key: str) -> None:
    """Ask which person, build their Beitrittserklärung, and say where it is.

    Args:
        document_key: The `app.domain.auto_attachments` key of the stored
            form, so the preview uses the same file a send would.

    Returns:
        None.
    """
    with connection_scope() as connection:
        people = sorted(
            (person for person in person_repo.list_all(connection) if person.active),
            key=person_name_key,
        )
        stored = leg_document_repo.get(connection, document_key)

    options = {person.id: person.display_name for person in people}

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
        ui.label("Beitrittserklärung ansehen").classes("text-lg font-bold")
        ui.label(
            "Erzeugt das Formular für eine Person, wie es ein Versand "
            "anhängen würde -- Seite 1 aus den gespeicherten Angaben, die "
            "übrigen Seiten aus der hinterlegten Vorlage. Es wird nichts "
            "versendet."
        ).classes("text-body2 text-grey-8")

        if stored is None:
            ui.label(
                "⚠ Es ist keine Vorlage hinterlegt, die Vorschau zeigt daher "
                "nur die ausgefüllte erste Seite. Einstellungen → Allgemein → "
                "Aufnahmeprozess → LEG-Dokumente."
            ).classes("text-warning text-body2")
        else:
            ui.label(f"Vorlage: {stored.filename}").classes("text-caption text-grey-7")

        person_select = ui.select(
            options, label="Person", with_input=True, value=people[0].id if people else None
        ).classes("w-full")
        result = ui.column().classes("w-full gap-1")

        def build() -> None:
            """Write the document and show where it landed.

            Returns:
                None.
            """
            if person_select.value is None:
                safe_notify("Bitte eine Person wählen.", type="warning")
                return
            with connection_scope() as connection:
                person = person_repo.get(connection, person_select.value)
                if person is None:
                    safe_notify("Diese Person gibt es nicht mehr.", type="warning")
                    return
                fields = gather(connection, person)
                document = leg_document_repo.get(connection, document_key)

            target = (
                OUTPUT_DIR / "Vorschau" / f"Beitrittserklaerung_{_safe_filename(person.display_name)}.pdf"
            )
            build_contract(fields, document.content if document else None, target)

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
    """Name the fields that came out blank, so a gap is not mistaken for a bug.

    A blank line on the form can mean two things -- nothing is stored, or
    something went wrong reading it -- and only the first is normal. Saying
    which fields are empty turns the preview into an answer rather than a
    new question.

    Args:
        fields: The gathered `ContractFields`.

    Returns:
        None.
    """
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
    """Hand one file to the system viewer.

    The only place this app opens a file. Everywhere else it writes into
    `output/` and names the path, which stays the behaviour here too -- this
    is the convenience on top, not the mechanism.

    Args:
        path: The file to open.

    Returns:
        None.
    """
    opener: Optional[object] = getattr(os, "startfile", None)
    if opener is None:
        safe_notify(f"Datei liegt unter: {path}", type="info")
        return
    try:
        opener(str(path))  # type: ignore[operator]
    except OSError as exc:
        safe_notify(f"Konnte die Datei nicht öffnen: {exc}", type="negative")
