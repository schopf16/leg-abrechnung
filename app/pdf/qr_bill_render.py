"""Builds a `qrbill.QRBill` and renders it as the bottom section of an A4 PDF page."""

import tempfile
from decimal import Decimal
from pathlib import Path

from qrbill import QRBill
from reportlab.graphics import renderPDF
from reportlab.pdfgen.canvas import Canvas
from svglib.svglib import svg2rlg

from app.models.participant import Participant
from app.models.settings import LegSettings


class QrBillConfigurationError(Exception):
    """Raised when the LEG settings are incomplete or invalid for a QR-bill.

    Typically means the administrator has not yet filled in the QR-IBAN or
    sender address on the "Einstellungen" page.
    """


def build_qr_bill(
    settings: LegSettings, participant: Participant, amount_chf: Decimal, reference: str
) -> QRBill:
    """Construct a `QRBill` for one invoice.

    Args:
        settings: LEG settings providing the creditor (payee) account and
            address.
        participant: The billed participant, used as the debtor address.
        amount_chf: Amount to collect, in Swiss francs.
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
            amount=str(amount_chf),
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

    Renders the QR-bill's official full-page SVG layout (which includes the
    perforation line and reserves its content to the bottom ~105mm of an A4
    page) and overlays it on top of whatever has already been drawn, so it
    must be called last for a given page.

    Args:
        canvas: Target canvas, already sized A4.
        bill: The `QRBill` to render.

    Returns:
        None.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        svg_path = Path(tmp_dir) / "qrbill.svg"
        bill.as_svg(str(svg_path), full_page=True)
        drawing = svg2rlg(str(svg_path))
        renderPDF.draw(drawing, canvas, 0, 0)
