"""Sending mail via SMTP, then filing a copy in the mailbox's Sent folder via IMAP.

SMTP alone leaves no trace in the mailbox (unlike Graph's `saveToSentItems`), so after a successful
send the same message is appended over IMAP. That copy failing never undoes the send: the mail is
out, so it is logged as a warning instead of being reported as a failed send.
"""

import asyncio
import email.policy
import imaplib
import logging
import mimetypes
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional, Sequence

from app.config import SECURITY_SSL, SmtpConfig
from app.emailing.errors import MailAuthError, MailError
from app.emailing.graph_client import MAX_INLINE_ATTACHMENT_BYTES, Attachment
from app.format_size import format_size

logger = logging.getLogger(__name__)

#: 7-bit transfer encoding (quoted-printable for umlauts): an 8-bit body is only allowed after the
#: server's 8BITMIME has been asked for, which `smtplib.send_message` does not do.
_POLICY = email.policy.default.clone(cte_type="7bit")

_TIMEOUT_SECONDS = 30.0

#: Used when the server marks no folder `\Sent` (RFC 6154); compared without case.
_USUAL_SENT_FOLDER_NAMES = (
    "sent",
    "sent items",
    "sent messages",
    "sent mail",
    "gesendet",
    "gesendete elemente",
    "gesendete objekte",
    "gesendete nachrichten",
)

#: One line of an IMAP LIST answer: `(\HasNoChildren \Sent) "/" "Sent"`.
_LIST_LINE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?P<delimiter>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$')

#: Monotonic time of the last send, for the pause between two messages of a batch.
_last_send_at: Optional[float] = None

#: Our own reference, so a test can move this clock without moving asyncio's.
_now = time.monotonic


def build_message(
    config: SmtpConfig,
    *,
    to_addresses: Sequence[str],
    to_name: str,
    subject: str,
    body: str,
    attachments: Sequence[Attachment] = (),
) -> EmailMessage:
    """The message as it goes out: plain text, display names, a Message-ID on the sender's domain."""
    if not to_addresses:
        raise MailError(f"Keine E-Mail-Adresse für {to_name or 'diesen Empfänger'} hinterlegt.")
    message = EmailMessage(policy=_POLICY)
    try:
        message["From"] = formataddr((_one_line(config.sender_name), config.sender_address))
        message["To"] = ", ".join(formataddr((_one_line(to_name), address)) for address in to_addresses)
    except (ValueError, UnicodeError) as exc:
        # A line break or an umlaut in a stored address: this recipient fails, not the whole batch.
        raise MailError(
            f"Ungültige E-Mail-Adresse für {to_name}: {', '.join(map(repr, to_addresses))}"
        ) from exc
    message["Subject"] = _one_line(subject)
    message["Date"] = formatdate(localtime=True)
    # Without a Message-ID on the own domain, providers' spam filters rate the mail worse.
    message["Message-ID"] = make_msgid(domain=config.sender_address.rpartition("@")[2])
    message.set_content(body)

    total_bytes = 0
    for attachment in attachments:
        try:
            content = attachment.path.read_bytes()
        except OSError as exc:
            raise MailError(f"Anhang {attachment.path} konnte nicht gelesen werden: {exc}") from exc
        total_bytes += len(content)
        content_type = mimetypes.guess_type(attachment.filename or str(attachment.path))[0]
        maintype, _, subtype = (content_type or "application/octet-stream").partition("/")
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=attachment.filename)
    # The same ceiling as Graph, so a message that goes out one way also goes out the other.
    if total_bytes > MAX_INLINE_ATTACHMENT_BYTES:
        names = ", ".join(a.filename for a in attachments)
        raise MailError(
            f"Anhänge ({names}) sind zusammen mit {format_size(total_bytes)} zu gross -- "
            f"erlaubt sind insgesamt {format_size(MAX_INLINE_ATTACHMENT_BYTES)}."
        )
    return message


async def send_email(
    config: SmtpConfig,
    *,
    to_addresses: Sequence[str],
    to_name: str,
    subject: str,
    body: str,
    attachments: Sequence[Attachment] = (),
) -> None:
    """Send one message to one contract party, then file a copy in the Sent folder."""
    message = build_message(
        config,
        to_addresses=to_addresses,
        to_name=to_name,
        subject=subject,
        body=body,
        attachments=attachments,
    )
    await _wait_for_send_slot(config.send_delay_seconds)
    try:
        await asyncio.to_thread(_send_sync, config, message)
    finally:
        _mark_sent()
    if config.imap_enabled:
        try:
            await asyncio.to_thread(_store_sent_copy_sync, config, message.as_bytes())
        except MailError as exc:
            logger.warning(
                "Mail an %s gesendet, aber nicht unter „Gesendet“ abgelegt: %s", message["To"], exc
            )


async def check_connection(config: SmtpConfig) -> str:
    """Log in to SMTP (and IMAP, if a copy is wanted) without sending; describe what was found."""
    await asyncio.to_thread(_check_smtp_sync, config)
    description = f"SMTP {config.host}:{config.port}, Absender {config.sender_address}"
    if not config.imap_enabled:
        return f"{description}; keine Kopie unter „Gesendet“."
    folder = await asyncio.to_thread(_check_imap_sync, config)
    return f"{description}; Kopie im IMAP-Ordner {folder}."


def _one_line(text: str) -> str:
    """A header value never carries a line break -- it would start a header of its own."""
    return " ".join(text.split())


async def _wait_for_send_slot(delay_seconds: float) -> None:
    """Keep the configured pause since the previous message, so a batch is not throttled or flagged."""
    if _last_send_at is None or delay_seconds <= 0:
        return
    remaining = delay_seconds - (_now() - _last_send_at)
    if remaining > 0:
        await asyncio.sleep(remaining)


def _mark_sent() -> None:
    """Remember when the last message went out."""
    global _last_send_at
    _last_send_at = _now()


def _open_smtp(config: SmtpConfig) -> smtplib.SMTP:
    """Connect, encrypt and log in; the caller closes the connection."""
    context = ssl.create_default_context()
    try:
        if config.security == SECURITY_SSL:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                config.host, config.port, timeout=_TIMEOUT_SECONDS, context=context
            )
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=_TIMEOUT_SECONDS)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"SMTP-Server {config.host}:{config.port} nicht erreichbar: {exc}") from exc
    if config.security != SECURITY_SSL:
        try:
            server.starttls(context=context)
        except (smtplib.SMTPException, OSError) as exc:
            server.close()
            raise MailError(
                f"SMTP-Server {config.host} bietet keine Verschlüsselung (STARTTLS) an: {exc}"
            ) from exc
    try:
        server.login(config.username, config.password)
    except smtplib.SMTPAuthenticationError as exc:
        server.close()
        raise MailAuthError(
            f"Anmeldung am SMTP-Server {config.host} fehlgeschlagen ({_reply(exc)}) -- "
            "Benutzername und Passwort prüfen."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        server.close()
        raise MailError(f"Anmeldung am SMTP-Server {config.host} nicht möglich: {exc}") from exc
    return server


def _send_sync(config: SmtpConfig, message: EmailMessage) -> None:
    """Hand the message to the SMTP server; map its refusals to our errors, with its own words."""
    recipients = message["To"]
    server = _open_smtp(config)
    try:
        refused = server.send_message(message)
    except smtplib.SMTPSenderRefused as exc:
        raise MailAuthError(
            f"Der SMTP-Server lehnt den Absender {config.sender_address} ab ({_reply(exc)})."
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        details = "; ".join(
            f"{address}: {_reply_of(code, text)}" for address, (code, text) in exc.recipients.items()
        )
        raise MailError(f"Empfänger abgelehnt -- {details}") from exc
    except smtplib.SMTPDataError as exc:
        # e.g. Infomaniak's outgoing filter: "550 5.2.0 Spam message rejected".
        raise MailError(
            f"Der SMTP-Server hat die Nachricht an {recipients} abgelehnt ({_reply(exc)})."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Versand an {recipients} fehlgeschlagen: {exc}") from exc
    finally:
        _quit(server)
    if refused:
        # Some addresses of a couple took it, so the message is out and must count as sent.
        logger.warning("SMTP-Server hat einzelne Empfänger abgelehnt: %s", refused)


def _check_smtp_sync(config: SmtpConfig) -> None:
    """Log in and out again."""
    _quit(_open_smtp(config))


def _quit(server: smtplib.SMTP) -> None:
    """Say goodbye; a server that already hung up is no error."""
    try:
        server.quit()
    except (smtplib.SMTPException, OSError):
        server.close()


def _open_imap(config: SmtpConfig) -> imaplib.IMAP4_SSL:
    """Connect and log in to IMAP over TLS with the SMTP credentials."""
    try:
        imap = imaplib.IMAP4_SSL(
            config.imap_host,
            config.imap_port,
            ssl_context=ssl.create_default_context(),
            timeout=_TIMEOUT_SECONDS,
        )
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError(f"IMAP-Server {config.imap_host}:{config.imap_port} nicht erreichbar: {exc}") from exc
    try:
        imap.login(config.username, config.password)
    except (imaplib.IMAP4.error, OSError) as exc:
        _logout(imap)
        raise MailError(f"Anmeldung am IMAP-Server {config.imap_host} fehlgeschlagen: {exc}") from exc
    return imap


def _sent_folder(config: SmtpConfig, imap: imaplib.IMAP4) -> str:
    """The configured folder, or the one the server reports as Sent."""
    if config.imap_sent_folder:
        return quote_mailbox(config.imap_sent_folder)
    try:
        status, lines = imap.list()
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError(f"IMAP-Ordnerliste nicht lesbar: {exc}") from exc
    folder = find_sent_folder(lines if status == "OK" else [])
    if folder is None:
        raise MailError(
            "Kein Ordner für gesendete Nachrichten gefunden -- den Ordnernamen als "
            "„imap_sent_folder“ in config.local.json eintragen."
        )
    return folder


def _store_sent_copy_sync(config: SmtpConfig, raw_message: bytes) -> None:
    """Append the sent message, marked read, to the Sent folder."""
    imap = _open_imap(config)
    try:
        folder = _sent_folder(config, imap)
        try:
            status, data = imap.append(
                folder, r"(\Seen)", imaplib.Time2Internaldate(time.time()), raw_message
            )
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailError(f"Ablage im IMAP-Ordner {folder} fehlgeschlagen: {exc}") from exc
        if status != "OK":
            raise MailError(f"Ablage im IMAP-Ordner {folder} abgelehnt: {data}")
    finally:
        _logout(imap)


def _check_imap_sync(config: SmtpConfig) -> str:
    """Log in to IMAP and find the Sent folder, without storing anything."""
    imap = _open_imap(config)
    try:
        return _sent_folder(config, imap)
    finally:
        _logout(imap)


def _logout(imap: imaplib.IMAP4) -> None:
    """Close the IMAP session; a dropped connection is no error."""
    try:
        imap.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


def find_sent_folder(list_lines: Sequence[object]) -> Optional[str]:
    """The Sent folder from an IMAP LIST answer, as the server spells it (quoted if needed)."""
    by_name: dict[str, str] = {}
    for line in list_lines:
        if not isinstance(line, bytes):
            continue  # a literal (tuple) name -- rare, and never the Sent folder in practice
        match = _LIST_LINE.match(line.decode("utf-8", "replace").strip())
        if match is None:
            continue
        name = match["name"].strip()
        if "\\sent" in match["flags"].lower().split():
            return name
        delimiter = match["delimiter"].strip('"')
        plain = name.strip('"')
        last = plain.rsplit(delimiter, 1)[-1] if delimiter and delimiter != "NIL" else plain
        by_name.setdefault(last.lower(), name)
    for usual in _USUAL_SENT_FOLDER_NAMES:
        if usual in by_name:
            return by_name[usual]
    return None


def quote_mailbox(name: str) -> str:
    """A mailbox name as an IMAP quoted string, unless it already is one."""
    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
        return name
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _reply(exc: smtplib.SMTPResponseException) -> str:
    """`550 5.2.0 Spam message rejected` -- the server's code and its own words."""
    return _reply_of(exc.smtp_code, exc.smtp_error)


def _reply_of(code: int, text: object) -> str:
    """One SMTP reply as readable text."""
    words = text.decode("utf-8", "replace") if isinstance(text, bytes) else str(text)
    return f"{code} {words}".strip()
