"""Builds a `qrbill.QRBill` and renders it as the bottom section of an A4 PDF page."""

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
    """Raised when the LEG settings are incomplete or invalid for a QR-bill."""


#: Maximum length of a name in the Swiss QR-bill standard. `qrbill` rejects
#: anything longer with a `ValueError`, which used to surface as a
#: "check the QR-IBAN and sender address" error -- a misleading message for
#: a problem that has nothing to do with either.
QR_NAME_MAX_LENGTH = 70


def qr_debtor_name(person) -> str:
    """The payer name to encode on the payment part, guaranteed to fit."""
    if len(person.display_name) <= QR_NAME_MAX_LENGTH:
        return person.display_name
    first = person.company or person.full_name
    return first[:QR_NAME_MAX_LENGTH]


def qr_debtor_name_note(person) -> str | None:
    """A German note if this person's payment-part name had to be shortened."""
    used = qr_debtor_name(person)
    if used == person.display_name:
        return None
    return (
        f"{person.display_name}: der Name ist für die QR-Rechnung zu lang "
        f"({len(person.display_name)} von höchstens {QR_NAME_MAX_LENGTH} Zeichen). "
        f"Auf dem Einzahlungsschein steht „{used}“; die Anschrift nennt weiterhin beide."
    )


def build_qr_bill(
    settings: LegSettings,
    leg: Leg,
    person: Person,
    amount_chf: Optional[Decimal],
    reference: str,
) -> QRBill:
    """Construct a `QRBill` for one person, billed under one LEG."""
    try:
        return QRBill(
            account=settings.qr_iban,
            creditor={
                "name": leg.name,
                # Street and house number separately, as the Swiss
                # standard has them and `qrbill` takes them: this
                # used to cram both into "street".
                "street": settings.address_street,
                "house_num": settings.address_house_number,
                "pcode": settings.address_zip,
                "city": settings.address_city,
                "country": settings.address_country or "CH",
            },
            debtor={
                "name": qr_debtor_name(person),
                "street": person.billing_street_with_number,
                "pcode": person.billing_postal_code,
                "city": person.billing_city,
                "country": person.billing_country or "CH",
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
    """Render a `QRBill` as the bottom payment section of the current canvas page."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        svg_path = Path(tmp_dir) / "qrbill.svg"
        bill.as_svg(str(svg_path), full_page=False)
        drawing = svg2rlg(str(svg_path))
        renderPDF.draw(drawing, canvas, 0, 0)
