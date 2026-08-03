"""Generates a credit note (Gutschrift) PDF for one billing run line item.

Unlike invoices, credit notes carry no QR-bill -- the LEG pays the
producer, not the other way round -- so the participant's IBAN is printed
as plain text for the administrator's own bank transfer (see also
`app.pdf.payment_list`).
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from reportlab.lib.units import mm

from app.domain.period import quarter_label
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.participant import Participant
from app.models.settings import LegSettings
from app.pdf.layout import (
    draw_intro_text,
    draw_items_table,
    draw_meta_block,
    draw_recipient_block,
    draw_sender_block,
    draw_title,
    new_canvas,
)


def generate_credit_pdf(
    run: BillingRun,
    item: BillingRunItem,
    participant: Participant,
    settings: LegSettings,
    output_path: Path,
) -> Path:
    """Render one "gutschrift" line item as a complete credit note PDF.

    Args:
        run: The billing run the item belongs to.
        item: A `BillingRunItem` with `kind == "gutschrift"`.
        participant: The credited participant (payout recipient).
        settings: Current LEG settings (sender address).
        output_path: Destination path for the generated PDF.

    Returns:
        `output_path`, for convenience.

    Raises:
        ValueError: If `item.kind` is not "gutschrift".
    """
    if item.kind != "gutschrift":
        raise ValueError(f"generate_credit_pdf requires kind='gutschrift', got {item.kind!r}")

    amount_chf = Decimal(item.amount_rappen) / 100
    period = quarter_label(run.period_year, run.period_quarter)
    canvas = new_canvas(output_path)

    draw_sender_block(canvas, settings)
    draw_recipient_block(canvas, participant)
    draw_meta_block(
        canvas,
        [
            f"Gutschrift Nr. {item.id}",
            f"Datum: {date.today().strftime('%d.%m.%Y')}",
            f"Periode: {period}",
        ],
    )

    y = draw_title(canvas, "Gutschrift")
    y = draw_intro_text(
        canvas,
        f"Für den im {period} lokal an Ihre Energiegemeinschaft gelieferten "
        "Strom schreiben wir Ihnen folgenden Betrag gut:",
        y,
    )
    y = draw_items_table(
        canvas,
        y,
        rows=[
            (
                "Lokal gelieferter LEG-Strom",
                f"{item.kwh:.3f} kWh x {item.price_rp_per_kwh:.2f} Rp./kWh",
                f"{amount_chf:.2f} CHF",
            )
        ],
        total_label="Total (keine MWST)",
        total_value=f"{amount_chf:.2f} CHF",
    )

    canvas.setFont("Helvetica", 9)
    canvas.drawString(20 * mm, y, "Auszahlung durch die LEG an folgende Bankverbindung:")
    y -= 14
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(20 * mm, y, f"IBAN: {participant.iban or '(keine IBAN hinterlegt)'}")

    canvas.showPage()
    canvas.save()
    return output_path
