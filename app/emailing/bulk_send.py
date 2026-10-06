"""Recipient resolution and send orchestration for broadcast/LEG emails and invoice emails."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from app.config import GraphConfig
from app.emailing import graph_client
from app.emailing.templates import placeholder_values, render_template
from app.models import billing_run as billing_run_repo
from app.models import cooperative_membership as cooperative_membership_repo
from app.models import email_log as email_log_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import assignment as assignment_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.person import Person


def list_broadcast_recipients(connection) -> list[Person]:
    """List the default recipients for an "an alle" broadcast."""
    return [p for p in person_repo.list_all(connection) if p.active and p.contact_emails]


def list_leg_recipients(connection, leg_id: int) -> list[Person]:
    """List the persons currently or soon assigned to any MeteringPoint of one LEG."""
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
        if person is not None and person.contact_emails:
            recipients.append(person)
    return recipients


def list_cooperative_recipients(connection) -> list[Person]:
    """List today's Genossenschaft members with an email address."""
    member_ids = cooperative_membership_repo.member_person_ids(connection)
    return [
        p for p in person_repo.list_all(connection) if p.id in member_ids and p.active and p.contact_emails
    ]


@dataclass
class EmailSendResult:
    """Outcome of one bulk-send call, for display in the GUI."""

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
    attachments: Sequence[graph_client.Attachment] = (),
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> EmailSendResult:
    """Send a personalized email to each of an explicit list of recipients."""
    result = EmailSendResult()
    total = len(recipients)
    if total == 0:
        return result

    access_token = await graph_client.get_access_token(config)
    sent_emails: list[str] = []

    for index, person in enumerate(recipients):
        try:
            values = placeholder_values(connection, person)
            rendered_subject = render_template(subject, values)
            rendered_body = render_template(body, values)
            await graph_client.send_email(
                config,
                access_token,
                to_addresses=person.contact_emails,
                to_name=person.display_name,
                subject=rendered_subject,
                body=rendered_body,
                attachments=attachments,
            )
            result.sent.append(person.display_name)
            sent_emails.extend(person.contact_emails)
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
            # One name per line: a filename may legally contain a comma,
            # so a comma-joined list could not be split back apart. Safe on
            # Windows, which forbids characters 1-31 in a filename; see
            # `app.models.email_log`.
            attachment_filenames="\n".join(a.filename for a in attachments) or None,
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
    """Send each billed person their own invoice PDF from one billing run."""
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
    """Force-resend one already-sent invoice, regardless of `email_sent_at`."""
    person = person_repo.get(connection, item.person_id)
    if person is None:
        raise ValueError(f"Person #{item.person_id} existiert nicht mehr.")
    if not person.contact_emails:
        raise ValueError(f"{person.display_name} hat keine E-Mail-Adresse hinterlegt.")
    if not item.pdf_path:
        raise ValueError("Für diese Position wurde noch kein PDF erzeugt.")

    leg = leg_repo.get(connection, run.leg_id)
    access_token = await graph_client.get_access_token(config)
    await _send_one_invoice_email(connection, config, access_token, run, item, person, leg, subject, body)


#: Invoice-email-only placeholders, not derived from `Person` -- see
#: `_invoice_placeholder_values`. Exposed here (rather than only inline in
#: that function) so the GUI can validate against the *complete* set of
#: valid placeholders for an invoice email, not just the person ones.
INVOICE_EXTRA_PLACEHOLDERS = ("leg", "quartal", "jahr", "betrag")


def invoice_skip_reason(person: Optional[Person], item: BillingRunItem) -> Optional[str]:
    """Decide whether a billing run item should be skipped, and why."""
    if person is None:
        return "Person existiert nicht mehr."
    if person.paper_invoice:
        return "bevorzugt Papierrechnung."
    if not person.contact_emails:
        return "keine E-Mail-Adresse hinterlegt."
    if not item.pdf_path:
        return "PDF noch nicht erzeugt."
    if item.email_sent_at:
        return f"bereits am {item.email_sent_at[:10]} per E-Mail gesendet."
    return None


def _invoice_placeholder_values(run: BillingRun, item: BillingRunItem, leg) -> dict[str, str]:
    """Build the invoice-specific placeholder values (not from `Person`)."""
    return {
        "leg": leg.name if leg else "?",
        "quartal": str(run.period_quarter),
        "jahr": str(run.period_year),
        "betrag": f"{item.net_amount_chf:.2f}",
    }


async def _send_one_invoice_email(
    connection, config, access_token, run, item, person, leg, subject, body
) -> None:
    """Render and send one person's invoice email, then record `email_sent_at`."""
    values = {**placeholder_values(connection, person), **_invoice_placeholder_values(run, item, leg)}
    await graph_client.send_email(
        config,
        access_token,
        to_addresses=person.contact_emails,
        to_name=person.display_name,
        subject=render_template(subject, values),
        body=render_template(body, values),
        attachments=[graph_client.Attachment(path=Path(item.pdf_path), filename=Path(item.pdf_path).name)],
    )
    billing_run_repo.set_item_email_sent_at(connection, item.id, datetime.now(timezone.utc).isoformat())
