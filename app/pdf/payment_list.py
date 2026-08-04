"""Generates the payment list (Zahlliste) PDF: one row per producer payout.

The administrator uses this list to actually transfer money to producers;
the app itself never initiates payments (project brief, section 6).
"""

from decimal import Decimal
from pathlib import Path

from reportlab.lib.units import mm

from app.domain.period import quarter_label
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.participant import Participant
from app.pdf.layout import PAGE_WIDTH, new_canvas

_LEFT_MARGIN = 20 * mm
_RIGHT_MARGIN = 20 * mm


def generate_payment_list_pdf(
    run: BillingRun,
    items: list[BillingRunItem],
    participants: dict[int, Participant],
    output_path: Path,
) -> Path:
    """Render the payout overview for all credit notes in a billing run.

    Args:
        run: The billing run to summarize.
        items: All line items of the run (only participants owed money by
            the LEG, i.e. `net_amount_rappen < 0`, are listed).
        participants: Participant lookup by id, for names and IBANs.
        output_path: Destination path for the generated PDF.

    Returns:
        `output_path`, for convenience.
    """
    credit_items = [i for i in items if i.is_owed_by_leg]
    period = quarter_label(run.period_year, run.period_quarter)

    canvas = new_canvas(output_path)
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(_LEFT_MARGIN, 270 * mm, f"Zahlliste {period}")

    y = 255 * mm
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(_LEFT_MARGIN, y, "Teilnehmer")
    canvas.drawString(_LEFT_MARGIN + 80 * mm, y, "IBAN")
    canvas.drawRightString(PAGE_WIDTH - _RIGHT_MARGIN, y, "Betrag (CHF)")
    y -= 6
    canvas.line(_LEFT_MARGIN, y, PAGE_WIDTH - _RIGHT_MARGIN, y)
    y -= 12

    canvas.setFont("Helvetica", 9)
    total_rappen = 0
    for item in credit_items:
        participant = participants.get(item.participant_id)
        name = participant.name if participant else f"Teilnehmer #{item.participant_id}"
        iban = participant.iban if participant else ""
        amount = Decimal(-item.net_amount_rappen) / 100
        total_rappen += -item.net_amount_rappen

        canvas.drawString(_LEFT_MARGIN, y, name)
        canvas.drawString(_LEFT_MARGIN + 80 * mm, y, iban)
        canvas.drawRightString(PAGE_WIDTH - _RIGHT_MARGIN, y, f"{amount:.2f}")
        y -= 14
        if y < 30 * mm:
            canvas.showPage()
            canvas.setFont("Helvetica", 9)
            y = 270 * mm

    y -= 4
    canvas.line(_LEFT_MARGIN, y, PAGE_WIDTH - _RIGHT_MARGIN, y)
    y -= 14
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(_LEFT_MARGIN, y, "Total Auszahlungen")
    canvas.drawRightString(PAGE_WIDTH - _RIGHT_MARGIN, y, f"{Decimal(total_rappen) / 100:.2f} CHF")

    canvas.showPage()
    canvas.save()
    return output_path
