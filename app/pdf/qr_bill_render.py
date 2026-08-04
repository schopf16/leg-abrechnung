"""Builds a `qrbill.QRBill` and renders it as the bottom section of an A4 PDF page.

When a participant is owed money by the LEG (a net credit), the QR-bill
still has to be printed for a consistent document layout, but must never
be usable to actually transfer money: `build_qr_bill` is called with
`amount=None`, which makes qrbill itself omit the amount from the encoded
QR data (an "open amount" bill nobody can auto-pay a fixed sum with), and
`draw_void_amount_overlay` additionally prints a visible "***.**" over
both amount fields so a human reading the printed page immediately sees
it is void, not merely blank by mistake.
"""

import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Optional

from qrbill import QRBill
from reportlab.graphics import renderPDF
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from svglib.svglib import svg2rlg

from app.models.participant import Participant
from app.models.settings import LegSettings
from app.pdf.layout import PAGE_HEIGHT

#: Placeholder text printed over a voided QR-bill's amount fields.
VOID_AMOUNT_TEXT = "***.**"

# Coordinates below mirror the "amount" blank-rectangle positions qrbill's
# own draw_bill() computes internally (qrbill/bill.py, draw_blank_rect calls
# for the "Amount" field). They are not exposed by the library, so are
# reproduced here from its fixed, standardized layout: bill margin 5mm,
# RECEIPT_WIDTH 62mm, currency_top at 73mm within the 106mm-tall bill
# block. Since `draw_qr_bill` places that 106mm-tall block flush with the
# bottom of the A4 page (297mm), a local y measured from the *top* of the
# block corresponds to page position `297 - 106 + local_y` from the page
# top -- i.e. the same numbers as if qrbill's own full_page=True layout
# (which uses that exact offset) had been used.
_RECEIPT_AMOUNT_BOX_MM = (30, 262, 27, 11)  # x, y (from page top), width, height
_PAYMENT_AMOUNT_BOX_MM = (79, 267, 40, 15)


class QrBillConfigurationError(Exception):
    """Raised when the LEG settings are incomplete or invalid for a QR-bill.

    Typically means the administrator has not yet filled in the QR-IBAN or
    sender address on the "Einstellungen" page.
    """


def build_qr_bill(
    settings: LegSettings,
    participant: Participant,
    amount_chf: Optional[Decimal],
    reference: str,
) -> QRBill:
    """Construct a `QRBill` for one participant.

    Args:
        settings: LEG settings providing the creditor (payee) account and
            address.
        participant: The billed participant, used as the debtor address.
        amount_chf: Amount to collect, in Swiss francs, or `None` to create
            a QR-bill with no fixed amount encoded (used when the
            participant is owed money by the LEG instead -- see
            `draw_void_amount_overlay`).
        reference: 27-digit QRR reference number, see
            `app.pdf.qr_reference.generate_qrr_reference`.

    Returns:
        A configured `QRBill` instance, ready for `as_svg`.

    Raises:
        QrBillConfigurationError: If the LEG settings or participant
            address are missing required fields, or the QR-IBAN is invalid.
    """
    try:
        return QRBill(
            account=settings.qr_iban,
            creditor={
                "name": settings.name,
                "street": settings.address_street,
                "pcode": settings.address_zip,
                "city": settings.address_city,
                "country": settings.address_country or "CH",
            },
            debtor={
                "name": participant.name,
                "street": participant.address_street,
                "pcode": participant.address_zip,
                "city": participant.address_city,
                "country": participant.address_country or "CH",
            },
            amount=str(amount_chf) if amount_chf is not None else None,
            reference_number=reference,
            language="de",
        )
    except ValueError as exc:
        raise QrBillConfigurationError(
            "QR-Rechnung konnte nicht erstellt werden -- bitte QR-IBAN und "
            f"Absenderadresse in den Einstellungen prüfen: {exc}"
        ) from exc


def draw_qr_bill(canvas: Canvas, bill: QRBill) -> None:
    """Render a `QRBill` as the bottom payment section of the current canvas page.

    Deliberately renders qrbill's *bill-only* SVG (``full_page=False``,
    sized 210x106mm) rather than its full-page variant: qrbill's
    full-page output paints an opaque white rectangle across the *entire*
    A4 page as a background (qrbill/bill.py, "Force white background"),
    which would silently erase any content already drawn on this canvas
    (letterhead, tables) when composited on top of it. The smaller
    bill-only drawing only ever covers its own 106mm-tall area, so placing
    it flush with the bottom of the page reserves exactly that area and
    nothing more.

    Must be called after all other content for the page has been drawn,
    and only once the caller has confirmed (see `app.pdf.layout.CONTENT_BOTTOM_Y`)
    that nothing else on the page extends into the bottom 106mm.

    Args:
        canvas: Target canvas, already sized A4.
        bill: The `QRBill` to render.

    Returns:
        None.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        svg_path = Path(tmp_dir) / "qrbill.svg"
        bill.as_svg(str(svg_path), full_page=False)
        drawing = svg2rlg(str(svg_path))
        renderPDF.draw(drawing, canvas, 0, 0)


def draw_void_amount_overlay(canvas: Canvas) -> None:
    """Print "***.**" over both of a voided QR-bill's blank amount fields.

    Must be called after `draw_qr_bill` (so it draws on top), and only for
    a bill built with `amount_chf=None`.

    Args:
        canvas: Target canvas, already holding a voided (open-amount) QR-bill.

    Returns:
        None.
    """
    canvas.setFont("Helvetica-Bold", 11)
    for x_mm, y_mm_from_top, width_mm, height_mm in (
        _RECEIPT_AMOUNT_BOX_MM,
        _PAYMENT_AMOUNT_BOX_MM,
    ):
        center_x = (x_mm + width_mm / 2) * mm
        center_y = PAGE_HEIGHT - (y_mm_from_top + height_mm / 2) * mm - 1.5
        canvas.drawCentredString(center_x, center_y, VOID_AMOUNT_TEXT)
