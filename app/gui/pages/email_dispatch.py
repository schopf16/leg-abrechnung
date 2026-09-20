"""E-Mail-Versand page: a guided, step-by-step assistant for sending a
personalized email to all persons or to one LEG's current members, plus
a history of past sends.

Every recipient gets their own, separate Graph API call (see
`app.emailing` module docstring) -- never CC/BCC -- so nobody sees who
else received the message. The recipient list resolved from "Alle
Personen"/"eine LEG" is only a starting suggestion: the administrator can
remove or add individual people before sending (step 2), which affects
only this one send, never the underlying LEG membership.

Step 3 ("E-Mail verfassen") also accepts attachments -- the same set for
every recipient of this send, with Microsoft's `app.emailing.
graph_client.MAX_INLINE_ATTACHMENT_BYTES` applying to their **total**.
They are kept in memory (`attachments`) until `do_send()` actually sends,
at which point each is written to a temp file (the shape
`graph_client.send_email` expects, matching how an invoice PDF is
attached) and removed again immediately afterwards, success or failure --
never left lying around.

What is attached is stated three times, and that is deliberate: once as a
named list on step 3, once in the step 4 validation summary, and once
directly above the send button on step 5. A real send went out without
its attachment and nothing on the final step said either way -- so the
last thing read before an irreversible action now names every file.
"""

import tempfile
from pathlib import Path
from typing import Optional

from nicegui import events, ui

from app.config import ConfigError, get_graph_config
from app.db.connection import connection_scope
from app.format_size import format_size
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


async def read_uploaded_file(file) -> tuple[str, bytes]:
    """Read one uploaded file into the `(name, content)` pair this page keeps.

    Deliberately a module-level function rather than inline in the upload
    handler: reading the event is exactly where this page broke silently
    once already. NiceGUI 3.16 replaced `event.name`/`event.content.read()`
    with `event.file.name`/`await event.file.read()`; the old call raised
    `AttributeError` inside the handler, which NiceGUI logs and swallows,
    so every broadcast went out without its attachment and nothing said so.

    Args:
        file: NiceGUI `FileUpload` (`UploadEventArguments.file`) -- needs
            `.name` and an awaitable `.read()`.

    Returns:
        `(filename, content bytes)`.
    """
    return file.name, await file.read()


def attachments_too_large(attachments: list[tuple[str, bytes]]) -> bool:
    """Whether this set exceeds what one Graph request can carry.

    Args:
        attachments: `(filename, content)` pairs.

    Returns:
        `True` if the total is over `graph_client.
        MAX_INLINE_ATTACHMENT_BYTES`. Stated on every step that shows the
        attachments, not just where it is enforced: being told at the send
        button that the last ten minutes were wasted is not good enough.
    """
    return sum(len(content) for _, content in attachments) > graph_client.MAX_INLINE_ATTACHMENT_BYTES


def describe_attachments(attachments: list[tuple[str, bytes]]) -> str:
    """State plainly whether anything is attached, and what.

    The one sentence shown before an irreversible send. Names every file
    rather than counting them: a send went out without its attachment and
    nothing on the final step said either way.

    Args:
        attachments: `(filename, content)` pairs, in the order chosen.

    Returns:
        A German one-liner, e.g. `"Mit 2 Anhängen (412 KB): a.pdf, b.pdf"`
        or `"Ohne Anhang."`.
    """
    if not attachments:
        return "Ohne Anhang."
    total = format_size(sum(len(content) for _, content in attachments))
    names = ", ".join(name for name, _ in attachments)
    if len(attachments) == 1:
        return f"Mit 1 Anhang ({total}): {names}"
    return f"Mit {len(attachments)} Anhängen ({total}): {names}"


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


def _validation_warnings(
    subject: str, body: str, recipients: list[Person]
) -> tuple[set[str], list[Person], list[tuple[Person, list[str]]]]:
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
        # The attachments are kept in memory, not written to temp files
        # until do_send() actually sends -- so navigating away without
        # sending never leaves a stray file behind, see do_send()'s
        # try/finally.
        attachments: list[tuple[str, bytes]] = []

        with ui.stepper().props("vertical").classes("w-full") as stepper:
            with ui.step("scope", title="Empfänger-Art wählen"):
                scope_select = ui.select(
                    {"all": "Alle Personen", "leg": "Personen einer LEG"},
                    label="Empfänger-Art",
                    value=None,
                ).classes("w-full max-w-sm")
                leg_select = ui.select(leg_options, label="LEG", value=None).classes("w-full max-w-sm")
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
                            recipients = bulk_send.list_leg_recipients(inner_connection, leg_select.value)
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
                                ui.button(icon="close", on_click=lambda p=person: remove_recipient(p)).props(
                                    "dense flat size=sm"
                                )
                        with ui.row().classes("w-full items-center gap-2 mt-2"):
                            add_select = ui.select(
                                add_options, label="Person hinzufügen", with_input=True
                            ).classes("flex-grow")
                            ui.button("Hinzufügen", on_click=lambda: add_recipient(add_select.value)).props(
                                "dense flat"
                            )

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
                ui.label(f"Verfügbare Platzhalter: {PLACEHOLDER_HINT}").classes("text-caption text-grey-6")
                subject_input = ui.input("Betreff").classes("w-full")
                body_textarea = ui.textarea("Nachricht").classes("w-full").props("rows=8")
                signature_select = ui.select(signature_options, label="Signatur", value=None).classes(
                    "w-full max-w-sm"
                )
                ui.label(
                    "Wird nach der Nachricht angehängt, ohne den Text oben zu "
                    "verändern -- unter „Kommunikation → Signaturen“ verwaltet."
                ).classes("text-caption text-grey-6")

                ui.label("Anhänge").classes("font-bold mt-2")
                attachment_list = ui.column().classes("w-full gap-1")

                def render_attachments() -> None:
                    """(Re-)render the attachment list and its running total.

                    Named and listed rather than summarised: the whole
                    reason this exists is that a send went out without its
                    attachment and nobody noticed.

                    Returns:
                        None.
                    """
                    attachment_list.clear()
                    with attachment_list:
                        if not attachments:
                            ui.label("Keine Anhänge.").classes("text-caption text-grey-6")
                            return
                        ui.label(describe_attachments(attachments)).classes("text-body2")
                        for index, (name, content) in enumerate(attachments):
                            with ui.row().classes("items-center gap-2"):
                                ui.label(f"📎 {name}").classes("text-body2")
                                ui.label(format_size(len(content))).classes("text-caption text-grey-6")
                                ui.button(
                                    "Entfernen",
                                    on_click=lambda i=index: remove_attachment(i),
                                ).props("dense flat color=negative size=sm")
                        if attachments_too_large(attachments):
                            ui.label(
                                f"⚠ Zusammen zu gross -- erlaubt sind "
                                f"{format_size(graph_client.MAX_INLINE_ATTACHMENT_BYTES)} pro "
                                "E-Mail. Bitte einen Anhang entfernen."
                            ).classes("text-negative text-body2")

                async def handle_attachment_upload(event: events.MultiUploadEventArguments) -> None:
                    """Keep every selected file in memory as this send's attachments.

                    One handler for the whole selection, and exactly one
                    `reset()` after it. Deliberately NOT a per-file
                    `on_upload` handler: NiceGUI only sets Quasar's `batch`
                    prop when `multiple` *and* `on_multi_upload` are given
                    (see `nicegui.elements.upload.Upload.__init__`), so
                    without this handler Quasar starts one request per file
                    in parallel -- and `reset()` aborts the requests still
                    in flight, silently attaching only the first file. Same
                    shape as `app.gui.pages.import_page`.

                    Async because `FileUpload.read()` is a coroutine since
                    NiceGUI 3.16 -- see `read_uploaded_file`.

                    Args:
                        event: NiceGUI upload event carrying every selected file.

                    Returns:
                        None.
                    """
                    added = []
                    for file in event.files:
                        name, content = await read_uploaded_file(file)
                        attachments.append((name, content))
                        added.append(name)
                    render_attachments()
                    upload_widget.reset()
                    if added:
                        safe_notify(
                            f"{len(added)} Anhang hinzugefügt: {added[0]}"
                            if len(added) == 1
                            else f"{len(added)} Anhänge hinzugefügt: {', '.join(added)}",
                            type="positive",
                        )

                def remove_attachment(index: int) -> None:
                    """Remove one attachment, by position in the list.

                    Args:
                        index: Position in `attachments`.

                    Returns:
                        None.
                    """
                    if 0 <= index < len(attachments):
                        removed = attachments.pop(index)[0]
                        safe_notify(f"Anhang „{removed}“ entfernt.", type="info")
                    render_attachments()

                def handle_attachment_rejected() -> None:
                    """Notify when the upload widget rejects a too-large file.

                    Returns:
                        None.
                    """
                    max_mb = graph_client.MAX_INLINE_ATTACHMENT_BYTES // 1024 // 1024
                    safe_notify(f"Anhang zu gross -- maximal {max_mb} MB.", type="negative")

                upload_widget = (
                    ui.upload(
                        label="Datei anhängen",
                        multiple=True,
                        on_multi_upload=handle_attachment_upload,
                        on_rejected=handle_attachment_rejected,
                        max_file_size=graph_client.MAX_INLINE_ATTACHMENT_BYTES,
                        # Bounds one selection, not the running total -- the
                        # app-side check below is still what actually decides.
                        max_total_size=graph_client.MAX_INLINE_ATTACHMENT_BYTES,
                        auto_upload=True,
                    )
                    .props("accept=* flat")
                    .classes("max-w-sm")
                )
                render_attachments()

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
                    unknown, invalid_emails, missing = _validation_warnings(subject, body, recipients)
                    with validation_container:
                        ui.label(f"Empfänger: {len(recipients)}").classes("font-bold")
                        ui.label(describe_attachments(attachments)).classes(
                            "text-body2 " + ("text-grey-7" if attachments else "text-grey-6")
                        )
                        if attachments_too_large(attachments):
                            ui.label(
                                "⛔ Die Anhänge sind zusammen zu gross -- so kann nicht "
                                "gesendet werden. Unter „E-Mail verfassen“ einen entfernen."
                            ).classes("text-negative text-body2 font-bold")
                        if recipients:
                            values = person_placeholder_values(recipients[0])
                            ui.label("Vorschau (für die erste Person in der Liste):").classes(
                                "text-caption text-grey-6 mt-2"
                            )
                            with ui.card().classes("w-full bg-grey-1"):
                                ui.label(render_template(subject, values)).classes("font-bold")
                                ui.label(render_template(body, values)).style("white-space: pre-wrap")
                        if unknown:
                            placeholder_list = ", ".join(f"{{{name}}}" for name in unknown)
                            ui.label(f"⚠ Unbekannte Platzhalter: {placeholder_list}").classes("text-negative")
                        for person in invalid_emails:
                            with ui.row().classes("items-center gap-2"):
                                ui.label(
                                    f"⚠ {person.display_name}: E-Mail-Adresse "
                                    f"ungültig ({person.contact_email or '-'})"
                                ).classes("text-negative text-body2")
                                ui.button("Bearbeiten", on_click=lambda p=person: fix_person(p)).props(
                                    "dense flat"
                                )
                        for person, fields in missing:
                            with ui.row().classes("items-center gap-2"):
                                ui.label(
                                    f"⚠ {person.display_name}: fehlende Angabe für {', '.join(fields)}"
                                ).classes("text-negative text-body2")
                                ui.button("Bearbeiten", on_click=lambda p=person: fix_person(p)).props(
                                    "dense flat"
                                )

                def go_to_send() -> None:
                    """Refresh the pre-send summary and advance to step 5.

                    Returns:
                        None.
                    """
                    refresh_send_summary()
                    stepper.next()

                with ui.stepper_navigation():
                    ui.button("Zurück", on_click=stepper.previous).props("flat")
                    ui.button("Weiter", on_click=go_to_send)

            with ui.step("send", title="Versenden"):
                # Re-rendered on entering this step rather than built once:
                # the attachment list can change on step 3 and this is the
                # last thing read before an irreversible send.
                send_summary = ui.column().classes("w-full gap-0 mb-2")

                def refresh_send_summary() -> None:
                    """State recipient count and attachments before sending.

                    Returns:
                        None.
                    """
                    send_summary.clear()
                    with send_summary:
                        ui.label(f"An {len(recipients)} Empfänger").classes("font-bold")
                        ui.label(describe_attachments(attachments)).classes(
                            "text-body2 " + ("" if attachments else "text-grey-6")
                        )
                        if attachments_too_large(attachments):
                            ui.label(
                                "⛔ Die Anhänge sind zusammen zu gross -- so kann nicht "
                                "gesendet werden. Unter „E-Mail verfassen“ einen entfernen."
                            ).classes("text-negative text-body2 font-bold")

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
                    if attachments_too_large(attachments):
                        safe_notify(
                            "Die Anhänge sind zusammen zu gross -- bitte unter "
                            "„E-Mail verfassen“ einen entfernen.",
                            type="negative",
                        )
                        return
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
                    temp_paths: list[Path] = []
                    graph_attachments: list[graph_client.Attachment] = []
                    for name, content in attachments:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(name).suffix) as tmp_file:
                            tmp_file.write(content)
                            temp_path = Path(tmp_file.name)
                        temp_paths.append(temp_path)
                        graph_attachments.append(graph_client.Attachment(path=temp_path, filename=name))

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
                                attachments=graph_attachments,
                                on_progress=on_progress,
                            )
                    except (graph_client.GraphAuthError, graph_client.GraphApiError) as exc:
                        safe_notify(str(exc), type="negative")
                        back_button.enable()
                        send_button.enable()
                        return
                    finally:
                        for temp_path in temp_paths:
                            temp_path.unlink(missing_ok=True)

                    back_button.enable()
                    with send_result_container:
                        with ui.card().classes("w-full"):
                            ui.label(f"{len(result.sent)} E-Mail(s) an Microsoft übergeben.").classes(
                                "font-bold"
                            )
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
                        if entry.attachment_filenames:
                            # Split on the exact character that joined them
                            # (see `bulk_send`), not `splitlines()`: that also
                            # breaks on U+0085/U+2028/U+2029, which are legal
                            # in a Windows filename and would render one
                            # attachment as two.
                            names = entry.attachment_filenames.split("\n")
                            label = "Anhang" if len(names) == 1 else f"{len(names)} Anhänge"
                            ui.label(f"{label}: {', '.join(names)}").classes("text-caption text-grey-7")
                        ui.label(", ".join(entry.recipient_emails) or "-")

        refresh_history()
