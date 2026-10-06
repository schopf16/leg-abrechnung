"""The one mail, shown before it goes: read it, change it, send it.

The administrator's own description of what they wanted: *"ich will einen link
anklicken, dann wird mir das mail präsentiert, das kann ich 1:1 versenden oder
noch anpassen, und dann per weiteren klick versenden."* So the subject and the
body arrive rendered and stay editable, the attachment can be looked at before
it goes, and **nothing is sent until the Senden button is pressed.**

`form_guard` is applied without `on_save` on purpose: a send cannot be taken
back, so Enter must not set one off (see CLAUDE.md on wiring Enter per action).
"""

import os
from pathlib import Path
from typing import Callable, Optional

from nicegui import ui

from app.config import ConfigError, get_graph_config
from app.db.connection import connection_scope
from app.domain import message_attachments
from app.domain.message_templates import DueMessage
from app.emailing.graph_client import GraphApiError, GraphAuthError
from app.emailing.person_send import send_person_message
from app.emailing.templates import (
    compose_with_signature,
    find_unknown_placeholders,
    placeholder_values,
    render_template,
)
from app.format_size import format_size
from app.gui.form_dialog import form_guard
from app.gui.placeholder_help import placeholders_for, render_placeholder_help
from app.gui.safe_notify import safe_notify
from app.models import signature as signature_repo
from app.models.person import Person
from app.paths import OUTPUT_DIR

#: Where a produced attachment is written before it is sent. Inside
#: `output/`, like every other document this app generates.
SEND_DIR = OUTPUT_DIR / "Versand"


def open_message_send_dialog(
    person: Person,
    due: DueMessage,
    occasion: str,
    *,
    on_sent: Optional[Callable[[], None]] = None,
) -> None:
    """Show one baustein's mail for this person, and send it on a click."""
    template = due.template

    with connection_scope() as connection:
        values = placeholder_values(connection, person)
        prepared = message_attachments.prepare(
            connection, person, template.auto_attachments, directory=SEND_DIR
        )
        chosen = signature_repo.get(connection, template.signature_id) if template.signature_id else None

    # Composed before it is shown, not on the way out: the promise of this
    # dialog is that what is read is what goes out, signature included.
    #
    # Compose **then** render, which is the order `app.emailing.bulk_send`
    # uses for the Rundmail -- it appends the signature and substitutes
    # afterwards. The other way round, a placeholder inside a signature
    # would go out literally here while working there.
    raw_body = compose_with_signature(template.body, chosen.content if chosen else "")
    rendered_body = render_template(raw_body, values)

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-3xl"):
        ui.label(f"{template.name} an {person.display_name}").classes("text-lg font-bold")
        recipients = ", ".join(person.contact_emails) or "— keine E-Mail-Adresse hinterlegt"
        ui.label(f"An: {recipients}").classes("text-caption text-grey-7")

        subject_input = ui.input("Betreff", value=render_template(template.subject, values)).classes("w-full")
        body_input = ui.textarea("Text", value=rendered_body).props("autogrow outlined").classes("w-full")
        if chosen is not None:
            ui.label(f"Signatur: {chosen.name}").classes("text-caption text-grey-7")

        # Over the composed text, since the signature takes part in the
        # substitution too.
        _render_placeholder_note(template.subject + "\n" + raw_body, occasion)
        render_placeholder_help(occasion)
        _render_attachments(prepared)

        error_label = ui.label("").classes("text-negative text-body2")

        async def do_send() -> None:
            """Send exactly this text, with exactly these attachments."""
            if not person.contact_emails:
                error_label.text = "Diese Person hat keine E-Mail-Adresse hinterlegt."
                return
            error_label.text = ""
            send_button.disable()
            try:
                config = get_graph_config()
            except ConfigError as exc:
                send_button.enable()
                error_label.text = f"E-Mail-Versand ist nicht konfiguriert: {exc}"
                return
            paths = [entry.path for entry in prepared if entry.path is not None]
            try:
                with connection_scope() as connection:
                    await send_person_message(
                        connection,
                        config,
                        person=person,
                        subject=subject_input.value,
                        body=body_input.value,
                        occasion=occasion,
                        step=template.step,
                        template_id=template.id,
                        attachment_paths=paths,
                    )
            except (GraphApiError, GraphAuthError) as exc:
                send_button.enable()
                error_label.text = f"Senden fehlgeschlagen: {exc}"
                return
            dialog.close()
            safe_notify(f"{template.name} an {person.display_name} gesendet.", type="positive")
            if on_sent:
                on_sent()

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            send_button = ui.button("Senden", on_click=do_send)

    form_guard(dialog)
    dialog.open()


def _render_placeholder_note(raw_text: str, occasion: str) -> None:
    """Name any placeholder the template uses that nothing can fill."""
    unknown = find_unknown_placeholders(raw_text, placeholders_for(occasion))
    if not unknown:
        return
    names = ", ".join("{" + name + "}" for name in sorted(unknown))
    ui.label(f"⚠ Unbekannte Platzhalter, sie gehen so hinaus: {names}").classes("text-warning text-body2")


def _render_attachments(prepared: list[message_attachments.PreparedAttachment]) -> None:
    """List the documents going along, and name any that could not be made."""
    if not prepared:
        ui.label("Ohne Anhang.").classes("text-caption text-grey-7")
        return
    with ui.column().classes("w-full gap-1 mt-1"):
        for entry in prepared:
            with ui.row().classes("items-center gap-2"):
                if entry.is_ready and entry.path is not None:
                    size = format_size(entry.path.stat().st_size)
                    ui.label(f"📎 {entry.filename} ({size})").classes("text-body2")
                    ui.button("Ansehen", on_click=lambda path=entry.path: _open(path)).props(
                        "flat dense color=primary"
                    )
                else:
                    # Named and still sendable: the administrator asked for a
                    # warning rather than a block.
                    ui.label(f"⚠ {entry.label} fehlt: {entry.problem}").classes("text-warning text-body2")
        if any(not entry.is_ready for entry in prepared):
            ui.label("Die Mail kann trotzdem gesendet werden.").classes("text-caption text-grey-7")


def _open(path: Path) -> None:
    """Hand one produced attachment to the system viewer."""
    opener = getattr(os, "startfile", None)
    if opener is None:
        safe_notify(f"Datei liegt unter: {path}", type="info")
        return
    try:
        opener(str(path))
    except OSError as exc:
        safe_notify(f"Konnte die Datei nicht öffnen: {exc}", type="negative")
