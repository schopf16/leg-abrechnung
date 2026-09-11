"""E-Mail-Versand page: a guided, step-by-step assistant for sending a
personalized email to all persons or to one LEG's current members, plus
a history of past sends.

Every recipient gets their own, separate Graph API call (see
`app.emailing` module docstring) -- never CC/BCC -- so nobody sees who
else received the message. The recipient list resolved from "Alle
Personen"/"eine LEG" is only a starting suggestion: the administrator can
remove or add individual people before sending (step 2), which affects
only this one send, never the underlying LEG membership.

Step 3 ("E-Mail verfassen") also accepts one optional attachment -- the
same file for every recipient of this send, up to `app.emailing.
graph_client.MAX_INLINE_ATTACHMENT_BYTES`. It is kept in memory
(`attachment_bytes`) until `do_send()` actually sends, at which point it
is written to a temp file (the shape `graph_client.send_email` expects,
matching how an invoice PDF is attached) and removed again immediately
afterwards, success or failure -- never left lying around.
"""

import tempfile
from pathlib import Path
from typing import Optional

from nicegui import ui

from app.config import ConfigError, get_graph_config
from app.db.connection import connection_scope
from app.emailing import bulk_send, graph_client
from app.emailing.templates import (
    PERSON_PLACEHOLDERS,
    find_invalid_email_addresses,
    find_unknown_placeholders,
    person_placeholder_values,
    render_template,
    validate_person_placeholders,
)
from app.gui.navigation import page_frame
from app.gui.person_form import open_person_form
from app.gui.safe_notify import safe_notify
from app.models import email_log as email_log_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.models import signature as signature_repo
from app.models.person import Person

#: Shown as a hint above the subject/body fields.
PLACEHOLDER_HINT = ", ".join(f"{{{name}}}" for name in PERSON_PLACEHOLDERS)

#: Classic plain-text signature delimiter (RFC 3676) -- some mail clients
#: recognize "-- " on its own line and render/strip a trailing signature
#: specially (e.g. dimmed, or omitted from a reply quote).
_SIGNATURE_DELIMITER = "\n\n-- \n"


def _compose_body(body: str, signature_content: str) -> str:
    """Append a signature to a message body, if one was chosen.

    Args:
        body: The composed message text, as typed (unrendered).
        signature_content: The chosen signature's text, or `""` if "Keine
            Signatur" is selected.

    Returns:
        `body` unchanged if `signature_content` is empty, else `body` with
        the signature appended after `_SIGNATURE_DELIMITER`.
    """
    if not signature_content.strip():
        return body
    return f"{body}{_SIGNATURE_DELIMITER}{signature_content}"


def _validation_warnings(subject: str, body: str, recipients: list[Person]) -> tuple[
    set[str], list[Person], list[tuple[Person, list[str]]]
]:
    """Compute every warning the "Validierung" step should show.

    Args:
        subject: Current subject text (with placeholders, unrendered).
        body: Current body text (with placeholders, unrendered).
        recipients: Current recipient list.

    Returns:
        `(unknown_placeholders, invalid_email_persons, missing_field_problems)`
        -- `missing_field_problems` merges subject- and body-derived
        problems per person (a person appears once with the union of
        their missing fields, even if different placeholders are missing
        in the subject vs. the body).
    """
    unknown = find_unknown_placeholders(subject, PERSON_PLACEHOLDERS) | find_unknown_placeholders(
        body, PERSON_PLACEHOLDERS
    )
    invalid_emails = find_invalid_email_addresses(recipients)
    merged: dict[int, tuple[Person, set[str]]] = {}
    for template in (subject, body):
        for person, fields in validate_person_placeholders(template, recipients):
            existing = merged.setdefault(person.id, (person, set()))
            existing[1].update(fields)
    missing = [(person, sorted(fields)) for person, fields in merged.values()]
    return unknown, invalid_emails, missing


@ui.page("/email-dispatch")
def email_dispatch_page() -> None:
    """Render the E-Mail-Versand assistant and sent-history page.

    Returns:
        None.
    """
    with page_frame("/email-dispatch", "E-Mail versenden"):
        ui.label(
            "Sendet eine persönliche E-Mail an alle Personen oder an die "
            "aktuellen Mitglieder einer LEG -- jede Person bekommt eine "
            "eigene E-Mail, niemand sieht die anderen Empfänger."
        ).classes("text-body2 text-grey-8")

        with connection_scope() as connection:
            leg_options = {leg.id: leg.name for leg in leg_repo.list_all(connection)}
            signatures_by_id = {s.id: s for s in signature_repo.list_all(connection)}
        signature_options = {None: "Keine Signatur", **{s.id: s.name for s in signatures_by_id.values()}}

        recipients: list[Person] = []
        # The attachment is kept in memory, not written to a temp file
        # until do_send() actually sends -- so navigating away without
        # sending never leaves a stray file behind, see do_send()'s
        # try/finally.
        attachment_bytes: Optional[bytes] = None
        attachment_filename: Optional[str] = None

        with ui.stepper().props("vertical").classes("w-full") as stepper:
            with ui.step("scope", title="Empfänger-Art wählen"):
                scope_select = ui.select(
                    {"all": "Alle Personen", "leg": "Personen einer LEG"},
                    label="Empfänger-Art", value=None,
                ).classes("w-full max-w-sm")
                leg_select = ui.select(leg_options, label="LEG", value=None).classes(
                    "w-full max-w-sm"
                )
                leg_select.bind_visibility_from(scope_select, "value", value="leg")

                def go_to_recipients() -> None:
                    """Resolve the initial recipient list and advance to step 2.

                    Returns:
                        None.
                    """
                    nonlocal recipients
                    if scope_select.value is None:
                        safe_notify("Bitte eine Empfänger-Art wählen.", type="warning")
                        return
                    if scope_select.value == "leg" and leg_select.value is None:
                        safe_notify("Bitte eine LEG wählen.", type="warning")
                        return
                    with connection_scope() as inner_connection:
                        if scope_select.value == "all":
                            recipients = bulk_send.list_broadcast_recipients(inner_connection)
                        else:
                            recipients = bulk_send.list_leg_recipients(
                                inner_connection, leg_select.value
                            )
                    refresh_recipients_step()
                    stepper.next()

                with ui.stepper_navigation():
                    ui.button("Weiter", on_click=go_to_recipients)

            with ui.step("recipients", title="Vorschau + manuelle Anpassung"):
                recipients_container = ui.column().classes("w-full gap-1")

                def remove_recipient(person: Person) -> None:
                    """Remove one person from this send's recipient list.

                    Args:
                        person: Person to remove.

                    Returns:
                        None.
                    """
                    recipients.remove(person)
                    refresh_recipients_step()

                def add_recipient(person_id: Optional[int]) -> None:
                    """Add one person to this send's recipient list.

                    Args:
                        person_id: Id of the person to add, or `None` if
                            nothing was selected.

                    Returns:
                        None.
                    """
                    if person_id is None:
                        return
                    with connection_scope() as inner_connection:
                        person = person_repo.get(inner_connection, person_id)
                    if person is not None and all(p.id != person.id for p in recipients):
                        recipients.append(person)
                    refresh_recipients_step()

                def refresh_recipients_step() -> None:
                    """(Re-)render the recipient list and the "add person" picker.

                    Returns:
                        None.
                    """
                    recipients_container.clear()
                    with connection_scope() as inner_connection:
                        already_ids = {p.id for p in recipients}
                        add_options = {
                            p.id: f"{p.display_name} ({p.contact_email})"
                            for p in person_repo.list_all(inner_connection)
                            if p.id not in already_ids and p.contact_email.strip()
                        }
                    with recipients_container:
                        if not recipients:
                            ui.label("Keine Empfänger.").classes("text-grey-6")
                        for person in list(recipients):
                            with ui.row().classes("w-full items-center gap-2"):
                                ui.label(f"{person.display_name} ({person.contact_email})").classes(
                                    "flex-grow"
                                )
                                ui.button(
                                    icon="close", on_click=lambda p=person: remove_recipient(p)
                                ).props("dense flat size=sm")
                        with ui.row().classes("w-full items-center gap-2 mt-2"):
                            add_select = ui.select(
                                add_options, label="Person hinzufügen", with_input=True
                            ).classes("flex-grow")
                            ui.button(
                                "Hinzufügen", on_click=lambda: add_recipient(add_select.value)
                            ).props("dense flat")

                def go_to_compose() -> None:
                    """Validate the recipient list and advance to step 3.

                    Returns:
                        None.
                    """
                    if not recipients:
                        safe_notify("Bitte mindestens einen Empfänger.", type="warning")
                        return
                    stepper.next()

                with ui.stepper_navigation():
                    ui.button("Zurück", on_click=stepper.previous).props("flat")
                    ui.button("Weiter", on_click=go_to_compose)

            with ui.step("compose", title="E-Mail verfassen"):
                ui.label(f"Verfügbare Platzhalter: {PLACEHOLDER_HINT}").classes(
                    "text-caption text-grey-6"
                )
                subject_input = ui.input("Betreff").classes("w-full")
                body_textarea = ui.textarea("Nachricht").classes("w-full").props("rows=8")
                signature_select = ui.select(
                    signature_options, label="Signatur", value=None
                ).classes("w-full max-w-sm")
                ui.label(
                    "Wird nach der Nachricht angehängt, ohne den Text oben zu "
                    "verändern -- unter „Kommunikation → Signaturen“ verwaltet."
                ).classes("text-caption text-grey-6")

                attachment_label = ui.label("Kein Anhang.").classes("text-caption text-grey-6")

                def handle_attachment_upload(event) -> None:
                    """Store an uploaded file's content in memory as this send's attachment.

                    Args:
                        event: NiceGUI `UploadEventArguments` -- `.content`
                            (readable) and `.name` (original filename).

                    Returns:
                        None.
                    """
                    nonlocal attachment_bytes, attachment_filename
                    attachment_bytes = event.content.read()
                    attachment_filename = event.name
                    attachment_label.text = (
                        f"Anhang: {attachment_filename} ({len(attachment_bytes) / 1024:.0f} KB)"
                    )
                    upload_widget.reset()

                def remove_attachment() -> None:
                    """Clear the currently chosen attachment, if any.

                    Returns:
                        None.
                    """
                    nonlocal attachment_bytes, attachment_filename
                    attachment_bytes = None
                    attachment_filename = None
                    attachment_label.text = "Kein Anhang."

                def handle_attachment_rejected() -> None:
                    """Notify when the upload widget rejects a too-large file.

                    Returns:
                        None.
                    """
                    max_mb = graph_client.MAX_INLINE_ATTACHMENT_BYTES // 1024 // 1024
                    safe_notify(f"Anhang zu gross -- maximal {max_mb} MB.", type="negative")

                with ui.row().classes("w-full items-center gap-2"):
                    upload_widget = ui.upload(
                        label="Anhang (optional)",
                        on_upload=handle_attachment_upload,
                        on_rejected=handle_attachment_rejected,
                        max_file_size=graph_client.MAX_INLINE_ATTACHMENT_BYTES,
                        auto_upload=True,
                    ).props("accept=* flat").classes("max-w-sm")
                    ui.button(icon="close", on_click=remove_attachment).props("dense flat").tooltip(
                        "Anhang entfernen"
                    )

                def go_to_validation() -> None:
                    """Validate the subject and advance to step 4.

                    Returns:
                        None.
                    """
                    if not subject_input.value.strip():
                        safe_notify("Bitte einen Betreff eingeben.", type="warning")
                        return
                    refresh_validation_step()
                    stepper.next()

                with ui.stepper_navigation():
                    ui.button("Zurück", on_click=stepper.previous).props("flat")
                    ui.button("Weiter", on_click=go_to_validation)

            with ui.step("validate", title="Validierung"):
                validation_container = ui.column().classes("w-full gap-2")

                def fix_person(person: Person) -> None:
                    """Open the shared Person dialog to fix a validation issue.

                    Args:
                        person: Person to edit.

                    Returns:
                        None.
                    """

                    def on_saved(saved_person: Person) -> None:
                        for index, existing in enumerate(recipients):
                            if existing.id == saved_person.id:
                                recipients[index] = saved_person
                                break
                        refresh_validation_step()

                    with connection_scope() as inner_connection:
                        existing_person = person_repo.get(inner_connection, person.id)
                    open_person_form(existing=existing_person, on_saved=on_saved)

                def refresh_validation_step() -> None:
                    """(Re-)render the preview and all validation warnings.

                    Returns:
                        None.
                    """
                    validation_container.clear()
                    subject = subject_input.value
                    signature = signatures_by_id.get(signature_select.value)
                    body = _compose_body(body_textarea.value, signature.content if signature else "")
                    unknown, invalid_emails, missing = _validation_warnings(
                        subject, body, recipients
                    )
                    with validation_container:
                        ui.label(f"Empfänger: {len(recipients)}").classes("font-bold")
                        ui.label(
                            f"Anhang: {attachment_filename}" if attachment_filename else "Kein Anhang."
                        ).classes("text-body2 text-grey-7")
                        if recipients:
                            values = person_placeholder_values(recipients[0])
                            ui.label(
                                "Vorschau (für die erste Person in der Liste):"
                            ).classes("text-caption text-grey-6 mt-2")
                            with ui.card().classes("w-full bg-grey-1"):
                                ui.label(render_template(subject, values)).classes("font-bold")
                                ui.label(render_template(body, values)).style(
                                    "white-space: pre-wrap"
                                )
                        if unknown:
                            placeholder_list = ", ".join(f"{{{name}}}" for name in unknown)
                            ui.label(f"⚠ Unbekannte Platzhalter: {placeholder_list}").classes(
                                "text-negative"
                            )
                        for person in invalid_emails:
                            with ui.row().classes("items-center gap-2"):
                                ui.label(
                                    f"⚠ {person.display_name}: E-Mail-Adresse "
                                    f"ungültig ({person.contact_email or '-'})"
                                ).classes("text-negative text-body2")
                                ui.button(
                                    "Bearbeiten", on_click=lambda p=person: fix_person(p)
                                ).props("dense flat")
                        for person, fields in missing:
                            with ui.row().classes("items-center gap-2"):
                                ui.label(
                                    f"⚠ {person.display_name}: fehlende Angabe "
                                    f"für {', '.join(fields)}"
                                ).classes("text-negative text-body2")
                                ui.button(
                                    "Bearbeiten", on_click=lambda p=person: fix_person(p)
                                ).props("dense flat")

                with ui.stepper_navigation():
                    ui.button("Zurück", on_click=stepper.previous).props("flat")
                    ui.button("Weiter", on_click=stepper.next)

            with ui.step("send", title="Versenden"):
                progress_bar = ui.linear_progress(value=0.0, show_value=False).classes("w-full")
                progress_bar.visible = False
                progress_label = ui.label("").classes("text-caption")
                progress_warning = ui.label(
                    "Bitte die App während des Versands nicht schliessen -- "
                    "jede Person erhält eine eigene E-Mail, das dauert bei "
                    "vielen Empfängern einen Moment."
                ).classes("text-caption text-warning")
                progress_warning.bind_visibility_from(progress_bar, "visible")
                send_result_container = ui.column().classes("w-full mt-2")

                async def do_send() -> None:
                    """Send the composed email to every current recipient.

                    Returns:
                        None.
                    """
                    send_button.disable()
                    send_result_container.clear()
                    try:
                        config = get_graph_config()
                    except ConfigError as exc:
                        safe_notify(str(exc), type="negative")
                        send_button.enable()
                        return

                    progress_bar.visible = True
                    progress_bar.value = 0.0
                    progress_label.text = f"0 von {len(recipients)} gesendet"
                    back_button.disable()

                    def on_progress(done: int, total: int) -> None:
                        progress_bar.value = done / total if total else 1.0
                        progress_label.text = f"{done} von {total} gesendet"

                    signature = signatures_by_id.get(signature_select.value)
                    final_body = _compose_body(body_textarea.value, signature.content if signature else "")

                    # Written to disk only for the duration of this one
                    # send -- graph_client.send_email expects a Path (the
                    # same shape as an invoice PDF), and the temp file is
                    # always removed again below, success or failure.
                    attachment_temp_path: Optional[Path] = None
                    if attachment_bytes is not None:
                        suffix = Path(attachment_filename).suffix
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                            tmp_file.write(attachment_bytes)
                            attachment_temp_path = Path(tmp_file.name)

                    try:
                        with connection_scope() as inner_connection:
                            result = await bulk_send.send_broadcast_email(
                                inner_connection,
                                config,
                                recipients,
                                subject_input.value,
                                final_body,
                                scope=scope_select.value,
                                leg_id=leg_select.value if scope_select.value == "leg" else None,
                                attachment_path=attachment_temp_path,
                                attachment_filename=attachment_filename,
                                on_progress=on_progress,
                            )
                    except (graph_client.GraphAuthError, graph_client.GraphApiError) as exc:
                        safe_notify(str(exc), type="negative")
                        back_button.enable()
                        send_button.enable()
                        return
                    finally:
                        if attachment_temp_path is not None:
                            attachment_temp_path.unlink(missing_ok=True)

                    back_button.enable()
                    with send_result_container:
                        with ui.card().classes("w-full"):
                            ui.label(
                                f"{len(result.sent)} E-Mail(s) an Microsoft übergeben."
                            ).classes("font-bold")
                            ui.label(
                                "„Übergeben“ heisst: von Microsoft zur Zustellung "
                                "angenommen -- ob eine Adresse tatsächlich existiert, "
                                "kann die App nicht prüfen."
                            ).classes("text-caption text-grey-6")
                            for line in result.errors:
                                ui.label(f"⚠ {line}").classes("text-negative text-body2")
                    refresh_history()

                send_button = ui.button("Jetzt senden", on_click=do_send)
                with ui.stepper_navigation():
                    back_button = ui.button("Zurück", on_click=stepper.previous).props("flat")

        ui.separator().classes("my-6")
        ui.label("Versand-Historie").classes("text-lg font-bold")
        history_container = ui.column().classes("w-full")

        def refresh_history() -> None:
            """(Re-)render the sent-history list.

            Returns:
                None.
            """
            history_container.clear()
            with connection_scope() as connection:
                entries = email_log_repo.list_all(connection)
                leg_names = {leg.id: leg.name for leg in leg_repo.list_all(connection)}
            with history_container:
                if not entries:
                    ui.label("Noch keine Versände.").classes("text-grey-6")
                for entry in entries:
                    scope_label = (
                        leg_names.get(entry.leg_id, "?") if entry.scope == "leg" else "Alle Personen"
                    )
                    title = (
                        f"{entry.sent_at[:16].replace('T', ' ')} -- {scope_label} -- "
                        f"„{entry.subject}“ ({entry.recipient_count} Empfänger)"
                    )
                    with ui.expansion(title).classes("w-full"):
                        if entry.attachment_filename:
                            ui.label(f"Anhang: {entry.attachment_filename}").classes(
                                "text-caption text-grey-7"
                            )
                        ui.label(", ".join(entry.recipient_emails) or "-")

        refresh_history()
