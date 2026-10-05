"""Generates the dunning notice PDF sent (or printed) for one person, covering every billing run item
included in that dunning notice."""

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
    """Render one dunning notice PDF covering every item in `candidate`."""
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
