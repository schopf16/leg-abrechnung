"""Sending one baustein to one person, and writing down that it went.

One message for the one contract party, both addresses of a couple in the
one message -- the rule the rest of `app.emailing` follows. The subject and
body arrive **already rendered and possibly edited**; they are sent verbatim,
because what the administrator read in the dialog is what has to go out.

Nothing here decides to send. It is called from a click and from nowhere else.
"""

import sqlite3
from pathlib import Path
from typing import Optional, Sequence

from app.config import MailConfig
from app.emailing import mail
from app.models import person_message_log as log_repo
from app.models.person import Person


async def send_person_message(
    connection: sqlite3.Connection,
    config: MailConfig,
    *,
    person: Person,
    subject: str,
    body: str,
    occasion: str,
    step: str,
    template_id: Optional[int],
    attachment_paths: Sequence[Path] = (),
) -> None:
    """Send one message to one person, then record it in the log."""
    if not person.contact_emails:
        raise mail.MailError(f"{person.display_name} hat keine E-Mail-Adresse hinterlegt.")

    token = await mail.prepare(config)
    await mail.send_email(
        config,
        token,
        to_addresses=person.contact_emails,
        to_name=person.display_name,
        subject=subject,
        body=body,
        attachments=[mail.Attachment(path=path, filename=path.name) for path in attachment_paths],
    )
    # Only after the send: the log answers "has this gone to this person",
    # and a failed attempt must not make it say yes.
    log_repo.record(
        connection,
        person_id=person.id,
        template_id=template_id,
        occasion=occasion,
        step=step,
        subject=subject,
        body=body,
        recipient_emails=list(person.contact_emails),
        attachment_filenames=[path.name for path in attachment_paths],
    )
