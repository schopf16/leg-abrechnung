"""Generates the dunning notice PDF sent (or printed) for one person, covering
every billing run item included in that dunning notice.

One page per open item, each ending in that item's own QR-bill -- not one
combined QR-bill for the consolidated total. This is deliberate: the bank
reconciliation in `app.domain.bank_reconciliation` decodes a QRR reference
back to exactly one billing run item (see `app.pdf.qr_reference.
parse_qrr_reference`), so a dunning notice must keep reusing each item's own,
already-generated reference rather than inventing a single reference for
a lumped total that could never be decoded back to which invoices it paid.
The "consolidation" the receivables plan describes is about the *letter*
(one mailing, one overview, one email) -- never about merging several
invoices' payment slips into one.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models import account_entry as account_entry_repo
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models.settings import LegSettings
from app.pdf.layout import (
    CONTENT_BOTTOM_Y,
    draw_intro_text,
    draw_meta_block,
    draw_recipient_block,
    draw_sender_block,
    draw_title,
    new_canvas,
)
from app.pdf.qr_bill_render import build_qr_bill, draw_qr_bill
from app.pdf.qr_reference import generate_qrr_reference


def generate_dunning_pdf(
    connection, candidate, rendered_body: str, settings: LegSettings, output_path
) -> Path:
    """Render one dunning notice PDF covering every item in `candidate`.

    Args:
        connection: Open SQLite connection.
        candidate: The `app.domain.dunning.DunningCandidate` to render.
        rendered_body: The dunning notice body text, placeholders already
            substituted (see `app.domain.dunning.send_dunning`),
            printed once per page above each item's QR-bill.
        settings: Current LEG-wide settings (address, QR-IBAN).
        output_path: Destination path for the generated PDF.

    Returns:
        `output_path`, for convenience.

    Raises:
        app.pdf.qr_bill_render.QrBillConfigurationError: If the LEG
            settings are missing required fields for a valid QR-bill.
    """
    person = candidate.person
    canvas = new_canvas(output_path)

    for item in candidate.items:
        remaining_rappen = account_entry_repo.get_remaining_for_item(
            connection, item.id, item.net_amount_rappen
        )
        if remaining_rappen == 0:
            # Already fully covered by payments/corrections linked to this
            # specific item -- nothing left to charge via its QR-bill, so
            # this item is skipped entirely (it should rarely still be a
            # dunning notice candidate at all, since the person-level balance gate
            # in `app.domain.dunning.list_due_dunnings` would
            # normally already exclude it; this only guards the case where
            # other open items on the same person keep the balance positive).
            continue

        run = billing_run_repo.get_run(connection, item.billing_run_id)
        leg = leg_repo.get(connection, run.leg_id)

        draw_sender_block(canvas, settings, leg)
        draw_recipient_block(canvas, person)
        draw_meta_block(
            canvas,
            [
                f"{candidate.level}. Mahnung",
                f"Datum: {date.today().strftime('%d.%m.%Y')}",
                f"Zu Abrechnung Nr. {item.id}",
                f"Kunden-Nr.: {person.formatted_customer_number}",
            ],
        )

        y = draw_title(canvas, f"{candidate.level}. Mahnung")
        for line in rendered_body.split("\n"):
            y = draw_intro_text(canvas, line, y)

        if y < CONTENT_BOTTOM_Y:
            canvas.showPage()

        reference = generate_qrr_reference(person.customer_number, run.id, item.id)
        bill = build_qr_bill(settings, leg, person, Decimal(remaining_rappen) / 100, reference)
        draw_qr_bill(canvas, bill)
        canvas.showPage()

    canvas.save()
    return output_path
