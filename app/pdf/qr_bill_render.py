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


#: Maximum length of a name in the Swiss QR-bill standard. `qrbill` rejects
#: anything longer with a `ValueError`, which used to surface as a
#: "check the QR-IBAN and sender address" error -- a misleading message for
#: a problem that has nothing to do with either.
QR_NAME_MAX_LENGTH = 70


def qr_debtor_name(person) -> str:
    """The payer name to encode on the payment part, guaranteed to fit.

    A couple's `display_name` holds both names ("Anna Muster und Beat
    Beispiel"), which can exceed the standard's 70 characters. Rather than
    let the whole document fail, the first named person is used alone: a
    shortened name on the payment slip is recoverable, an invoice that
    cannot be produced is not. The address block above still shows both
    names, so the recipient is in no doubt who is meant, and the QRR
    reference -- not the name -- is what identifies the payment.

    Args:
        person: The billed `app.models.person.Person`.

    Returns:
        `display_name` when it fits, otherwise the first named person's
        name, hard-truncated as a last resort.
    """
    if len(person.display_name) <= QR_NAME_MAX_LENGTH:
        return person.display_name
    first = person.company or person.full_name
    return first[:QR_NAME_MAX_LENGTH]


def qr_debtor_name_note(person) -> str | None:
    """A German note if this person's payment-part name had to be shortened.

    Args:
        person: The billed `app.models.person.Person`.

    Returns:
        A message naming what was printed instead, or `None` if the full
        name fitted. Reported through `app.pdf.export_service.ExportResult.
        errors` so a shortened name is never a silent change.
    """
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
    """Construct a `QRBill` for one person, billed under one LEG.

    Args:
        settings: LEG-wide settings providing the creditor (payee) account
            and address (shared across all LEGs).
        leg: The LEG this document is billed under, providing the
            creditor name.
        person: The billed person, whose billing address becomes the
            debtor address.
        amount_chf: Amount to collect, in Swiss francs, or `None` to create
            a QR-bill with no fixed amount encoded (an "open amount" bill).
        reference: 27-digit QRR reference number, see
            `app.pdf.qr_reference.generate_qrr_reference`.

    Returns:
        A configured `QRBill` instance, ready for `as_svg`.

    Raises:
        QrBillConfigurationError: If the LEG settings or the person's
            billing address are missing required fields, or the QR-IBAN
            is invalid.
    """
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
