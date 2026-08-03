"""Generates a Swiss-QR-bill invoice PDF for one billing run line item."""

from datetime import date
from decimal import Decimal
from pathlib import Path

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
from app.pdf.qr_bill_render import build_qr_bill, draw_qr_bill
from app.pdf.qr_reference import generate_qrr_reference


def generate_invoice_pdf(
    run: BillingRun,
    item: BillingRunItem,
    participant: Participant,
    settings: LegSettings,
    output_path: Path,
) -> Path:
    """Render one "rechnung" line item as a complete QR-invoice PDF.

    Args:
        run: The billing run the item belongs to.
        item: A `BillingRunItem` with `kind == "rechnung"`.
        participant: The billed participant (invoice recipient).
        settings: Current LEG settings (sender, QR-IBAN).
        output_path: Destination path for the generated PDF.

    Returns:
        `output_path`, for convenience.

    Raises:
        ValueError: If `item.kind` is not "rechnung".
        app.pdf.qr_bill_render.QrBillConfigurationError: If the LEG
            settings are missing required fields for a valid QR-bill.
    """
    if item.kind != "rechnung":
        raise ValueError(f"generate_invoice_pdf requires kind='rechnung', got {item.kind!r}")

    amount_chf = Decimal(item.amount_rappen) / 100
    reference = generate_qrr_reference(participant.id, run.id, item.id)
    bill = build_qr_bill(settings, participant, amount_chf, reference)

    period = quarter_label(run.period_year, run.period_quarter)
    canvas = new_canvas(output_path)

    draw_sender_block(canvas, settings)
    draw_recipient_block(canvas, participant)
    draw_meta_block(
        canvas,
        [
            f"Rechnung Nr. {item.id}",
            f"Datum: {date.today().strftime('%d.%m.%Y')}",
            f"Periode: {period}",
        ],
    )

    y = draw_title(canvas, "Rechnung")
    y = draw_intro_text(
        canvas,
        f"Für den im {period} lokal bezogenen Strom aus Ihrer Energiegemeinschaft "
        "stellen wir Ihnen folgenden Betrag in Rechnung:",
        y,
    )
    draw_items_table(
        canvas,
        y,
        rows=[
            (
                "Lokal bezogener LEG-Strom",
                f"{item.kwh:.3f} kWh x {item.price_rp_per_kwh:.2f} Rp./kWh",
                f"{amount_chf:.2f} CHF",
            )
        ],
        total_label="Total (keine MWST)",
        total_value=f"{amount_chf:.2f} CHF",
    )

    draw_qr_bill(canvas, bill)

    canvas.showPage()
    canvas.save()
    return output_path
