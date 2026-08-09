"""Builds a `qrbill.QRBill` and renders it as the bottom section of an A4 PDF page.

Only used when the person actually owes the LEG money -- a credit or a
zero balance has nothing to pay via a payment slip, so `app.pdf.
person_bill_pdf` skips this module entirely in that case rather than
printing a voided QR-bill (see that module's docstring).
"""

import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Optional

from qrbill import QRBill
from reportlab.graphics import renderPDF
from reportlab.pdfgen.canvas import Canvas
from svglib.svglib import svg2rlg

from app.models.leg import Leg
from app.models.person import Person
from app.models.settings import LegSettings


class QrBillConfigurationError(Exception):
    """Raised when the LEG settings are incomplete or invalid for a QR-bill.

    Typically means the administrator has not yet filled in the QR-IBAN or
    sender address on the "Einstellungen" page.
    """


def build_qr_bill(
    settings: LegSettings,
    leg: Leg,
    person: Person,
    amount_chf: Optional[Decimal],
    reference: str,
) -> QRBill:
    """Construct a `QRBill` for one person, billed under one LEG.

    Args:
        settings: LEG-wide settings providing the creditor (payee) account
            and address (shared across all LEGs).
        leg: The LEG this document is billed under, providing the
            creditor name.
        person: The billed person, whose Rechnungsadresse becomes the
            debtor address.
        amount_chf: Amount to collect, in Swiss francs, or `None` to create
            a QR-bill with no fixed amount encoded (an "open amount" bill).
        reference: 27-digit QRR reference number, see
            `app.pdf.qr_reference.generate_qrr_reference`.

    Returns:
        A configured `QRBill` instance, ready for `as_svg`.

    Raises:
        QrBillConfigurationError: If the LEG settings or the person's
            Rechnungsadresse are missing required fields, or the QR-IBAN
            is invalid.
    """
    try:
        return QRBill(
            account=settings.qr_iban,
            creditor={
                "name": leg.name,
                "street": settings.address_street,
                "pcode": settings.address_zip,
                "city": settings.address_city,
                "country": settings.address_country or "CH",
            },
            debtor={
                "name": person.anzeige_name,
                "street": person.rechnungsadresse_strasse_vollstaendig,
                "pcode": person.rechnungsadresse_plz,
                "city": person.rechnungsadresse_ort,
                "country": person.rechnungsadresse_land or "CH",
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
