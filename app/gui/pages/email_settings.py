"""Settings page for the email transport: Microsoft 365 (Graph) or SMTP with a copy via IMAP.

The values live in the gitignored `config.local.json`, not in the database: they are secrets, and
the database travels in backups and data exports.
"""

from nicegui import ui

from app.config import (
    DEFAULT_SMTP_PORTS,
    MAX_SEND_DELAY_SECONDS,
    SECURITY_SSL,
    SECURITY_STARTTLS,
    TRANSPORT_GRAPH,
    TRANSPORT_SMTP,
    ConfigError,
    mail_config_from,
    read_mail_settings,
    save_mail_settings,
)
from app.domain.email_validation import validate_email
from app.emailing import mail
from app.gui.navigation import page_frame
from app.gui.safe_notify import safe_notify

SAMPLE_SUBJECT = "Kontrollversand der Abrechnungssoftware"


def sample_message_body(sender_name: str) -> str:
    """A test mail that reads like a real one -- a bare one-liner is what spam filters reject."""
    return (
        "Guten Tag\n\n"
        "Diese Nachricht wurde zur Kontrolle des E-Mail-Versands der Abrechnungssoftware "
        "verschickt. Sie müssen nichts unternehmen.\n\n"
        "Freundliche Grüsse\n"
        f"{sender_name}\n"
    )


@ui.page("/email-settings")
def email_settings_page() -> None:
    """Render the email transport settings page."""
    with page_frame("/email-settings", "E-Mail-Versand"):
        current = read_mail_settings()

        ui.label(
            "Über welchen Weg die App E-Mails verschickt. Die Angaben stehen in "
            "config.local.json auf diesem Computer, nicht in der Datenbank -- "
            "sie gehen also weder ins Backup noch in den Datenexport."
        ).classes("text-body2 text-grey-8")

        transport = ui.toggle(
            {TRANSPORT_GRAPH: "Microsoft 365 (Graph)", TRANSPORT_SMTP: "SMTP / IMAP"},
            value=current["mail_transport"]
            if current["mail_transport"] in (TRANSPORT_GRAPH, TRANSPORT_SMTP)
            else TRANSPORT_GRAPH,
        ).classes("mt-4")

        with ui.card().classes("w-full max-w-lg") as graph_card:
            ui.label("Microsoft 365 (Graph)").classes("text-body1 font-bold")
            ui.label(
                "App-Registrierung in Microsoft Entra mit der Berechtigung Mail.Send -- "
                "siehe README, Abschnitt 5."
            ).classes("text-caption text-grey-7")
            graph_tenant_id = ui.input("Tenant-ID", value=current["graph_tenant_id"]).classes("w-full")
            graph_client_id = ui.input("Client-ID", value=current["graph_client_id"]).classes("w-full")
            graph_client_secret = ui.input(
                "Client-Secret",
                value=current["graph_client_secret"],
                password=True,
                password_toggle_button=True,
            ).classes("w-full")
            graph_sender_address = ui.input(
                "Absender-Adresse", value=current["graph_sender_address"]
            ).classes("w-full")
            graph_sender_name = ui.input("Absender-Name", value=current["graph_sender_name"]).classes(
                "w-full"
            )
        graph_card.bind_visibility_from(transport, "value", value=TRANSPORT_GRAPH)

        with ui.card().classes("w-full max-w-lg") as smtp_card:
            ui.label("SMTP (Versand)").classes("text-body1 font-bold")
            ui.label(
                "Die Angaben des E-Mail-Anbieters, z. B. Infomaniak: mail.infomaniak.com, Port 465, SSL/TLS."
            ).classes("text-caption text-grey-7")
            smtp_host = ui.input("Server", value=current["smtp_host"]).classes("w-full")
            with ui.row().classes("w-full gap-2 no-wrap"):
                smtp_security = ui.select(
                    {SECURITY_SSL: "SSL/TLS", SECURITY_STARTTLS: "STARTTLS"},
                    label="Verschlüsselung",
                    value=current["smtp_security"]
                    if current["smtp_security"] in DEFAULT_SMTP_PORTS
                    else SECURITY_SSL,
                ).classes("flex-grow")
                smtp_port = ui.number(
                    "Port", value=_number(current["smtp_port"]), min=1, max=65535, step=1, format="%d"
                ).classes("w-28")
            smtp_username = ui.input("Benutzername", value=current["smtp_username"]).classes("w-full")
            smtp_username.props('hint="Meist die vollständige E-Mail-Adresse."')
            smtp_password = ui.input(
                "Passwort", value=current["smtp_password"], password=True, password_toggle_button=True
            ).classes("w-full")
            smtp_password.props('hint="Mit Zwei-Faktor-Anmeldung: ein App-Passwort des Anbieters."')
            smtp_sender_address = ui.input("Absender-Adresse", value=current["smtp_sender_address"]).classes(
                "w-full"
            )
            smtp_sender_name = ui.input("Absender-Name", value=current["smtp_sender_name"]).classes("w-full")
            smtp_sender_name.props('hint="Erscheint beim Empfänger als Absender, z. B. der Vereinsname."')
            smtp_send_delay = ui.number(
                "Pause zwischen zwei E-Mails (Sekunden)",
                value=_number(current["smtp_send_delay_seconds"]),
                min=0,
                max=MAX_SEND_DELAY_SECONDS,
                step=0.5,
                format="%.1f",
            ).classes("w-full")
            smtp_send_delay.props(
                'hint="Damit der Anbieter einen Massenversand nicht bremst oder als Spam einstuft."'
            )

            ui.label("IMAP (Kopie unter „Gesendet“)").classes("text-body1 font-bold mt-4")
            ui.label(
                "Per SMTP versandte E-Mails erscheinen sonst nicht im Postfach. Gleiche "
                "Anmeldung wie für SMTP."
            ).classes("text-caption text-grey-7")
            imap_enabled = ui.switch("Kopie unter „Gesendet“ ablegen", value=bool(current["imap_enabled"]))
            with ui.column().classes("w-full gap-0") as imap_fields:
                with ui.row().classes("w-full gap-2 no-wrap"):
                    imap_host = ui.input("IMAP-Server", value=current["imap_host"]).classes("flex-grow")
                    imap_host.props('hint="Leer: wie SMTP-Server."')
                    imap_port = ui.number(
                        "IMAP-Port",
                        value=_number(current["imap_port"]),
                        min=1,
                        max=65535,
                        step=1,
                        format="%d",
                    ).classes("w-28")
                imap_sent_folder = ui.input("Ordner", value=current["imap_sent_folder"]).classes("w-full")
                imap_sent_folder.props('hint="Leer: wird automatisch gefunden."')
            imap_fields.bind_visibility_from(imap_enabled, "value")
        smtp_card.bind_visibility_from(transport, "value", value=TRANSPORT_SMTP)

        def follow_security(event) -> None:
            """Switch the port with the encryption, unless someone typed a port of their own."""
            if smtp_port.value in (None, *DEFAULT_SMTP_PORTS.values()):
                smtp_port.value = DEFAULT_SMTP_PORTS.get(event.value, DEFAULT_SMTP_PORTS[SECURITY_SSL])

        smtp_security.on_value_change(follow_security)

        def form_values() -> dict[str, object]:
            """What the form says, in the keys of config.local.json."""
            return {
                "mail_transport": transport.value,
                "graph_tenant_id": graph_tenant_id.value or "",
                "graph_client_id": graph_client_id.value or "",
                "graph_client_secret": graph_client_secret.value or "",
                "graph_sender_address": graph_sender_address.value or "",
                "graph_sender_name": graph_sender_name.value or "",
                "smtp_host": smtp_host.value or "",
                "smtp_port": _whole(smtp_port.value),
                "smtp_security": smtp_security.value,
                "smtp_username": smtp_username.value or "",
                "smtp_password": smtp_password.value or "",
                "smtp_sender_address": smtp_sender_address.value or "",
                "smtp_sender_name": smtp_sender_name.value or "",
                "smtp_send_delay_seconds": smtp_send_delay.value,
                "imap_enabled": bool(imap_enabled.value),
                "imap_host": imap_host.value or "",
                "imap_port": _whole(imap_port.value),
                "imap_sent_folder": imap_sent_folder.value or "",
            }

        with ui.card().classes("w-full max-w-lg"):
            result_label = ui.label("").classes("text-caption").mark("email-settings-result")

            def show(text: str, *, ok: bool) -> None:
                """One line of feedback under the buttons, green or red."""
                result_label.text = text
                result_label.classes(
                    add="text-positive" if ok else "text-negative",
                    remove="text-negative" if ok else "text-positive",
                )

            def save() -> None:
                """Validate the chosen transport and write it to config.local.json."""
                try:
                    save_mail_settings(form_values())
                except (ConfigError, ValueError, OSError) as exc:
                    show(f"Nicht gespeichert: {exc}", ok=False)
                    return
                show("Gespeichert.", ok=True)
                safe_notify("E-Mail-Versand gespeichert.", type="positive")

            async def test_connection() -> None:
                """Log in with what the form says, saved or not, without sending."""
                show("Prüfe Verbindung...", ok=True)
                try:
                    config = mail_config_from(form_values())
                    description = await mail.check_connection(config)
                except (ConfigError, mail.MailError) as exc:
                    show(str(exc), ok=False)
                    return
                show(f"Verbindung erfolgreich -- {description}", ok=True)

            test_recipient = ui.input("Test-E-Mail an").classes("w-full")
            test_recipient.props('hint="Leer: an die Absender-Adresse."')

            async def send_test() -> None:
                """Send one realistic test message with what the form says."""
                try:
                    config = mail_config_from(form_values())
                except ConfigError as exc:
                    show(str(exc), ok=False)
                    return
                recipient = (test_recipient.value or "").strip() or config.sender_address
                problem = validate_email(recipient)
                if problem:
                    show(f"Test-E-Mail nicht gesendet: {problem}", ok=False)
                    return
                show(f"Sende Test-E-Mail an {recipient}...", ok=True)
                try:
                    token = await mail.prepare(config)
                    await mail.send_email(
                        config,
                        token,
                        to_addresses=[recipient],
                        to_name="",
                        subject=SAMPLE_SUBJECT,
                        body=sample_message_body(config.sender_name or config.sender_address),
                    )
                except mail.MailError as exc:
                    show(f"Test-E-Mail nicht gesendet: {exc}", ok=False)
                    return
                show(
                    f"Test-E-Mail an {recipient} gesendet -- bitte Posteingang und „Gesendet“ prüfen.",
                    ok=True,
                )

            with ui.row().classes("gap-2"):
                ui.button("Speichern", on_click=save)
                ui.button("Verbindung testen", on_click=test_connection).props("outline")
                ui.button("Test-E-Mail senden", on_click=send_test).props("outline")


def _number(value: object) -> object:
    """A stored number for a number field; anything unreadable shows as empty."""
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _whole(value: object) -> object:
    """A port from a number field: a whole number, or empty so the default applies."""
    return int(value) if isinstance(value, (int, float)) else ""
