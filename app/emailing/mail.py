"""The one door for sending email: picks Microsoft Graph or SMTP from the config's type.

Callers do `prepare` once per batch, then `send_email` per contract party, and catch
`MailAuthError` (abort the batch) before `MailError` (skip this recipient).
"""

from typing import Sequence

from app.config import GraphConfig, MailConfig, SmtpConfig
from app.emailing import graph_client, smtp_client
from app.emailing.errors import MailAuthError, MailError
from app.emailing.graph_client import MAX_INLINE_ATTACHMENT_BYTES, Attachment

__all__ = [
    "MAX_INLINE_ATTACHMENT_BYTES",
    "Attachment",
    "MailAuthError",
    "MailError",
    "check_connection",
    "prepare",
    "send_email",
]


async def prepare(config: MailConfig) -> str:
    """Graph's access token for the batch; SMTP logs in per message, so nothing to prepare."""
    if isinstance(config, GraphConfig):
        return await graph_client.get_access_token(config)
    _require_known(config)
    return ""


async def send_email(
    config: MailConfig,
    token: str,
    *,
    to_addresses: Sequence[str],
    to_name: str,
    subject: str,
    body: str,
    attachments: Sequence[Attachment] = (),
) -> None:
    """Send one plain-text email to exactly one contract party (both addresses of a couple)."""
    if isinstance(config, GraphConfig):
        await graph_client.send_email(
            config,
            token,
            to_addresses=to_addresses,
            to_name=to_name,
            subject=subject,
            body=body,
            attachments=attachments,
        )
        return
    _require_known(config)
    await smtp_client.send_email(
        config,
        to_addresses=to_addresses,
        to_name=to_name,
        subject=subject,
        body=body,
        attachments=attachments,
    )


async def check_connection(config: MailConfig) -> str:
    """Log in without sending anything; a sentence on what works, for the settings page."""
    if isinstance(config, GraphConfig):
        await graph_client.get_access_token(config)
        return f"Microsoft Graph, Absender {config.sender_address}."
    _require_known(config)
    return await smtp_client.check_connection(config)


def _require_known(config: object) -> None:
    """A config of a third kind is a programming error, not a mail failure."""
    if not isinstance(config, SmtpConfig):
        raise TypeError(f"Unbekannte E-Mail-Konfiguration: {type(config).__name__}")
