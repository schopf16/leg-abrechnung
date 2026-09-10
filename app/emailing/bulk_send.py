"""Recipient resolution and send orchestration for broadcast/LEG emails
and invoice emails.

See `app.emailing` (module docstring) for why every send is a separate,
individual `graph_client.send_email` call, and `app.emailing.templates`
for the `{placeholder}` substitution used in both flows.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from app.config import GraphConfig
from app.emailing import graph_client
from app.emailing.templates import person_placeholder_values, render_template
from app.models import billing_run as billing_run_repo
from app.models import email_log as email_log_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import assignment as assignment_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.person import Person


def list_broadcast_recipients(connection) -> list[Person]:
    """List the default recipients for an "an alle" broadcast.

    Args:
        connection: Open SQLite connection.

    Returns:
        Active persons with a non-empty contact email, in
        `person_repo.list_all`'s order. Purely a starting suggestion --
        the caller (GUI) lets the administrator add/remove individual
        recipients before actually sending, see `app.gui.pages.
        email_versand`.
    """
    return [
        p for p in person_repo.list_all(connection) if p.active and p.contact_email.strip()
    ]


def list_leg_recipients(connection, leg_id: int) -> list[Person]:
    """List the persons currently or soon assigned to any MeteringPoint of one LEG.

    Args:
        connection: Open SQLite connection.
        leg_id: LEG to resolve members for.

    Returns:
        Persons with a current-or-upcoming Assignment (`Assignment.
        is_current_or_upcoming` -- an assignment entered ahead of its
        start date, e.g. next quarter's move-ins prepared in advance,
        counts too) to a MeteringPoint in this LEG, deduplicated (a person can
        hold more than one MeteringPoint in the same LEG), with a non-empty
        contact email. Purely a starting suggestion, same caveat as
        `list_broadcast_recipients`.
    """
    now = datetime.now()
    metering_points = [mp for mp in metering_point_repo.list_all(connection) if mp.leg_id == leg_id]
    person_ids: dict[int, None] = {}  # insertion-ordered set
    for metering_point in metering_points:
        for assignment in assignment_repo.list_for_metering_point(connection, metering_point.id):
            if assignment.is_current_or_upcoming(now):
                person_ids.setdefault(assignment.person_id, None)

    recipients = []
    for person_id in person_ids:
        person = person_repo.get(connection, person_id)
        if person is not None and person.contact_email.strip():
            recipients.append(person)
    return recipients


@dataclass
class EmailSendResult:
    """Outcome of one bulk-send call, for display in the GUI.

    Attributes:
        sent: Display names of recipients the email was successfully
            handed off to Microsoft for (see `graph_client.send_email`'s
            docstring for why that is not the same as "delivered").
        skipped: `"<name>: <reason>"` for recipients never attempted
            (e.g. paper-invoice preference, missing email, already sent).
        errors: `"<name>: <error>"` for recipients where sending was
            attempted but failed.
    """

    sent: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def send_broadcast_email(
    connection,
    config: GraphConfig,
    recipients: list[Person],
    subject: str,
    body: str,
    *,
    scope: str,
    leg_id: Optional[int] = None,
    attachment_path: Optional[Path] = None,
    attachment_filename: Optional[str] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> EmailSendResult:
    """Send a personalized email to each of an explicit list of recipients.

    Args:
        connection: Open SQLite connection.
        config: Graph API credentials.
        recipients: The final, administrator-confirmed recipient list
            (already resolved/edited by the caller -- see
            `list_broadcast_recipients`/`list_leg_recipients`).
        subject: Email subject, may contain `{placeholder}`s.
        body: Email body, may contain `{placeholder}`s.
        scope: `"alle"` or `"leg"`, recorded in the sent-history log.
        leg_id: LEG id, if `scope == "leg"`, else `None`.
        attachment_path: Optional file attached to every recipient's copy
            (the same one for the whole batch -- see `app.gui.pages.
            email_versand`, which lets the administrator pick one file
            for the send). `None` for no attachment.
        attachment_filename: Filename shown for the attachment, required
            if `attachment_path` is given.
        on_progress: Called as `on_progress(done, total)` after each send
            attempt (success, skip, or failure) -- lets the GUI show a
            live progress bar. Optional.

    Returns:
        The `EmailSendResult`. A history entry (see `app.models.
        email_log`) is always written for the recipients actually
        reached, even if some individual sends failed.

    Raises:
        graph_client.GraphAuthError: If the very first send fails due to
            invalid credentials -- every subsequent attempt would fail
            for the same reason, so the whole run aborts immediately
            rather than working through the rest of the list.
    """
    result = EmailSendResult()
    total = len(recipients)
    if total == 0:
        return result

    access_token = await graph_client.get_access_token(config)
    sent_emails: list[str] = []

    for index, person in enumerate(recipients):
        try:
            rendered_subject = render_template(subject, person_placeholder_values(person))
            rendered_body = render_template(body, person_placeholder_values(person))
            await graph_client.send_email(
                config,
                access_token,
                to_address=person.contact_email,
                to_name=person.display_name,
                subject=rendered_subject,
                body=rendered_body,
                attachment_path=attachment_path,
                attachment_filename=attachment_filename,
            )
            result.sent.append(person.display_name)
            sent_emails.append(person.contact_email)
        except graph_client.GraphAuthError:
            if on_progress:
                on_progress(index + 1, total)
            raise
        except graph_client.GraphApiError as exc:
            result.errors.append(f"{person.display_name}: {exc}")
        if on_progress:
            on_progress(index + 1, total)

    if sent_emails:
        email_log_repo.create(
            connection,
            scope=scope,
            leg_id=leg_id,
            subject=subject,
            body=body,
            recipient_emails=sent_emails,
            attachment_filename=attachment_filename,
        )
    return result


async def send_invoice_emails(
    connection,
    config: GraphConfig,
    run: BillingRun,
    subject: str,
    body: str,
    *,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> EmailSendResult:
    """Send each billed person their own invoice PDF from one billing run.

    Args:
        connection: Open SQLite connection.
        config: Graph API credentials.
        run: The billing run to send invoices for.
        subject: Email subject, may contain `{placeholder}`s (Person- and
            invoice-context ones, see `_invoice_placeholder_values`).
        body: Email body, may contain `{placeholder}`s.
        on_progress: Called as `on_progress(done, total)` after each item
            is processed (sent, skipped, or failed). Optional.

    Returns:
        The `EmailSendResult`. A line item is skipped (not attempted,
        never counts as an error) if: the person has opted for paper
        invoices (`Person.paper_invoice`), has no contact email, has no
        generated PDF yet (`item.pdf_path` empty -- PDFs must be
        exported first), or was already emailed (`item.email_sent_at`
        set -- see `resend_invoice_email` to force a specific resend).

    Raises:
        graph_client.GraphAuthError: Aborts the whole run immediately,
            same reasoning as `send_broadcast_email`.
    """
    result = EmailSendResult()
    items = billing_run_repo.list_items(connection, run.id)
    total = len(items)
    if total == 0:
        return result

    leg = leg_repo.get(connection, run.leg_id)
    access_token = await graph_client.get_access_token(config)

    for index, item in enumerate(items):
        person = person_repo.get(connection, item.person_id)
        skip_reason = invoice_skip_reason(person, item)
        if skip_reason is not None:
            name = person.display_name if person else f"Person #{item.person_id}"
            result.skipped.append(f"{name}: {skip_reason}")
            if on_progress:
                on_progress(index + 1, total)
            continue

        try:
            await _send_one_invoice_email(
                connection, config, access_token, run, item, person, leg, subject, body
            )
            result.sent.append(person.display_name)
        except graph_client.GraphAuthError:
            if on_progress:
                on_progress(index + 1, total)
            raise
        except graph_client.GraphApiError as exc:
            result.errors.append(f"{person.display_name}: {exc}")
        if on_progress:
            on_progress(index + 1, total)

    return result


async def resend_invoice_email(
    connection, config: GraphConfig, run: BillingRun, item: BillingRunItem, subject: str, body: str
) -> None:
    """Force-resend one already-sent invoice, regardless of `email_sent_at`.

    Covers the realistic "I never got my invoice" case without weakening
    `send_invoice_emails`'s automatic duplicate-send protection, which
    keeps skipping already-sent items unconditionally.

    Args:
        connection: Open SQLite connection.
        config: Graph API credentials.
        run: The billing run `item` belongs to.
        item: The specific line item to resend.
        subject: Email subject, may contain `{placeholder}`s.
        body: Email body, may contain `{placeholder}`s.

    Returns:
        None.

    Raises:
        ValueError: If the person, their email, or the PDF is missing --
            an explicit resend should never silently no-op.
        graph_client.GraphAuthError: If credentials are invalid.
        graph_client.GraphApiError: For any other send failure.
    """
    person = person_repo.get(connection, item.person_id)
    if person is None:
        raise ValueError(f"Person #{item.person_id} existiert nicht mehr.")
    if not person.contact_email.strip():
        raise ValueError(f"{person.display_name} hat keine E-Mail-Adresse hinterlegt.")
    if not item.pdf_path:
        raise ValueError("Für diese Position wurde noch kein PDF erzeugt.")

    leg = leg_repo.get(connection, run.leg_id)
    access_token = await graph_client.get_access_token(config)
    await _send_one_invoice_email(
        connection, config, access_token, run, item, person, leg, subject, body
    )


#: Invoice-email-only placeholders, not derived from `Person` -- see
#: `_invoice_placeholder_values`. Exposed here (rather than only inline in
#: that function) so the GUI can validate against the *complete* set of
#: valid placeholders for an invoice email, not just the person ones.
INVOICE_EXTRA_PLACEHOLDERS = ("leg", "quartal", "jahr", "betrag")


def invoice_skip_reason(person: Optional[Person], item: BillingRunItem) -> Optional[str]:
    """Decide whether a billing run item should be skipped, and why.

    Also used by the GUI (see `app.gui.pages.billing`) to preview what
    a bulk send would do before actually sending anything.

    Args:
        person: The billed person, or `None` if they no longer exist.
        item: The line item to check.

    Returns:
        A human-readable (German) skip reason, or `None` if it should be sent.
    """
    if person is None:
        return "Person existiert nicht mehr."
    if person.paper_invoice:
        return "bevorzugt Papierrechnung."
    if not person.contact_email.strip():
        return "keine E-Mail-Adresse hinterlegt."
    if not item.pdf_path:
        return "PDF noch nicht erzeugt."
    if item.email_sent_at:
        return f"bereits am {item.email_sent_at[:10]} per E-Mail gesendet."
    return None


def _invoice_placeholder_values(run: BillingRun, item: BillingRunItem, leg) -> dict[str, str]:
    """Build the invoice-specific placeholder values (not from `Person`).

    Args:
        run: The billing run `item` belongs to.
        item: The line item being emailed.
        leg: The `Leg` this run belongs to, or `None` if it was deleted.

    Returns:
        `{"leg": ..., "quartal": ..., "jahr": ..., "betrag": ...}`.
    """
    return {
        "leg": leg.name if leg else "?",
        "quartal": str(run.period_quarter),
        "jahr": str(run.period_year),
        "betrag": f"{item.net_amount_chf:.2f}",
    }


async def _send_one_invoice_email(
    connection, config, access_token, run, item, person, leg, subject, body
) -> None:
    """Render and send one person's invoice email, then record `email_sent_at`.

    Args:
        connection: Open SQLite connection.
        config: Graph API credentials.
        access_token: Bearer token from `graph_client.get_access_token`.
        run: The billing run `item` belongs to.
        item: The line item being emailed.
        person: The billed person.
        leg: The `Leg` this run belongs to, or `None`.
        subject: Email subject template.
        body: Email body template.

    Returns:
        None.
    """
    values = {**person_placeholder_values(person), **_invoice_placeholder_values(run, item, leg)}
    await graph_client.send_email(
        config,
        access_token,
        to_address=person.contact_email,
        to_name=person.display_name,
        subject=render_template(subject, values),
        body=render_template(body, values),
        attachment_path=Path(item.pdf_path),
        attachment_filename=Path(item.pdf_path).name,
    )
    billing_run_repo.set_item_email_sent_at(
        connection, item.id, datetime.now(timezone.utc).isoformat()
    )
