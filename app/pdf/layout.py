"""Shared A4 letterhead and table drawing helpers for the combined
per-person billing document (see `app.pdf.person_bill_pdf`).
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from app.models.leg import Leg
from app.models.person import Person
from app.models.settings import LegSettings

PAGE_WIDTH, PAGE_HEIGHT = A4

#: Vertical position, from the top, where the free content area ends and
#: the Swiss QR-bill's reserved bottom section (105mm tall) begins.
CONTENT_BOTTOM_Y = 108 * mm

_LEFT_MARGIN = 20 * mm
_RIGHT_MARGIN = 20 * mm


def new_canvas(path) -> Canvas:
    """Create a new A4 PDF canvas at the given filesystem path.

    Args:
        path: Destination path (`str` or `Path`) for the PDF file.

    Returns:
        A `reportlab.pdfgen.canvas.Canvas` ready to draw on, page size A4.
    """
    return Canvas(str(path), pagesize=A4)


def draw_sender_block(canvas: Canvas, settings: LegSettings, leg: Leg) -> None:
    """Draw the sender address in the top-left corner.

    The displayed name is the LEG's own name (invoices are per-LEG, see
    `app.models.leg`); address is shared across all LEGs (`settings`).

    Args:
        canvas: Target canvas.
        settings: LEG-wide settings providing the sender address.
        leg: The LEG this document is billed under.

    Returns:
        None.
    """
    y = PAGE_HEIGHT - 20 * mm
    canvas.setFont("Helvetica", 8)
    for line in (
        leg.name,
        settings.address_street,
        f"{settings.address_zip} {settings.address_city}",
    ):
        canvas.drawString(_LEFT_MARGIN, y, line)
        y -= 10


def draw_recipient_block(canvas: Canvas, person: Person) -> None:
    """Draw the recipient's billing address, positioned for a windowed envelope.

    Args:
        canvas: Target canvas.
        person: Recipient of the document.

    Returns:
        None.
    """
    y = PAGE_HEIGHT - 55 * mm
    canvas.setFont("Helvetica", 10)
    lines = [
        *person.adressblock_zeilen,
        person.rechnungsadresse_strasse,
        f"{person.rechnungsadresse_plz} {person.rechnungsadresse_ort}",
    ]
    for line in lines:
        if line.strip():
            canvas.drawString(_LEFT_MARGIN, y, line)
            y -= 12


def draw_meta_block(canvas: Canvas, lines: list[str]) -> None:
    """Draw a right-aligned metadata block (document number, date, period).

    Args:
        canvas: Target canvas.
        lines: Lines of text to display, top to bottom.

    Returns:
        None.
    """
    y = PAGE_HEIGHT - 20 * mm
    canvas.setFont("Helvetica", 9)
    for line in lines:
        canvas.drawRightString(PAGE_WIDTH - _RIGHT_MARGIN, y, line)
        y -= 12


def draw_title(canvas: Canvas, title: str, y_mm_from_top: float = 90) -> float:
    """Draw the document title (e.g. "Rechnung" or "Gutschrift").

    Args:
        canvas: Target canvas.
        title: Title text.
        y_mm_from_top: Vertical position, in millimeters from the top of
            the page.

    Returns:
        The y-coordinate (in points, from the page bottom) directly below
        the title, for placing subsequent content.
    """
    y = PAGE_HEIGHT - y_mm_from_top * mm
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(_LEFT_MARGIN, y, title)
    return y - 10 * mm


def draw_intro_text(canvas: Canvas, text: str, top_y: float) -> float:
    """Draw a paragraph of intro text below the title.

    Args:
        canvas: Target canvas.
        text: Text to display (single line; caller pre-wraps if needed).
        top_y: Y-coordinate (points from page bottom) to start at.

    Returns:
        The y-coordinate directly below the drawn text.
    """
    canvas.setFont("Helvetica", 10)
    canvas.drawString(_LEFT_MARGIN, top_y, text)
    return top_y - 10 * mm


def draw_monthly_table(
    canvas: Canvas,
    top_y: float,
    section_title: str,
    rows: list[tuple[str, str, str, str]],
    subtotal_label: str,
    subtotal_value: str,
) -> float:
    """Draw a per-month energy table (Monat | kWh | Rp./kWh | Betrag) with a subtotal.

    Args:
        canvas: Target canvas.
        top_y: Y-coordinate (points from page bottom) of the section's top edge.
        section_title: Section heading, e.g. "Bezug" or "Vergütung (Produktion)".
        rows: `(month_label, kwh_text, price_text, amount_text)` tuples,
            one per calendar month of the billing period.
        subtotal_label: Label for the subtotal row, e.g. "Zwischensumme Bezug".
        subtotal_value: Formatted subtotal amount, e.g. "123.45 CHF". This
            is a *display* figure only -- see the module docstring of
            `app.domain.billing` for why rounding never happens here.

    Returns:
        The y-coordinate directly below the section.
    """
    col_month_x = _LEFT_MARGIN
    col_kwh_x = PAGE_WIDTH - 95 * mm
    col_price_x = PAGE_WIDTH - 60 * mm
    col_amount_x = PAGE_WIDTH - _RIGHT_MARGIN

    y = top_y
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(_LEFT_MARGIN, y, section_title)
    y -= 8 * mm

    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(col_month_x, y, "Monat")
    canvas.drawRightString(col_kwh_x, y, "kWh")
    canvas.drawRightString(col_price_x, y, "Rp./kWh")
    canvas.drawRightString(col_amount_x, y, "Betrag (CHF)")
    y -= 6
    canvas.line(_LEFT_MARGIN, y, PAGE_WIDTH - _RIGHT_MARGIN, y)
    y -= 12

    canvas.setFont("Helvetica", 9)
    for month_label, kwh_text, price_text, amount_text in rows:
        canvas.drawString(col_month_x, y, month_label)
        canvas.drawRightString(col_kwh_x, y, kwh_text)
        canvas.drawRightString(col_price_x, y, price_text)
        canvas.drawRightString(col_amount_x, y, amount_text)
        y -= 14

    y -= 4
    canvas.line(_LEFT_MARGIN, y, PAGE_WIDTH - _RIGHT_MARGIN, y)
    y -= 14
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(col_month_x, y, subtotal_label)
    canvas.drawRightString(col_amount_x, y, subtotal_value)
    return y - 12 * mm


def draw_net_settlement(
    canvas: Canvas, top_y: float, label: str, value: str, note: str
) -> float:
    """Draw the final, rounded net settlement line and an explanatory note.

    This is the only place a rounded monetary figure appears on the page
    (see `app.domain.billing`): everything above it is either an unrounded
    display figure or a per-month kWh quantity.

    Args:
        canvas: Target canvas.
        top_y: Y-coordinate (points from page bottom) to start at.
        label: Label for the net amount, e.g. "Netto-Betrag (keine MWST)".
        value: Formatted, rounded net amount, e.g. "34.50 CHF".
        note: Short explanatory sentence shown below the amount.

    Returns:
        The y-coordinate directly below the drawn section.
    """
    y = top_y
    canvas.line(_LEFT_MARGIN, y, PAGE_WIDTH - _RIGHT_MARGIN, y)
    y -= 16
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(_LEFT_MARGIN, y, label)
    canvas.drawRightString(PAGE_WIDTH - _RIGHT_MARGIN, y, value)
    y -= 8 * mm
    canvas.setFont("Helvetica", 9)
    for line in note.split("\n"):
        canvas.drawString(_LEFT_MARGIN, y, line)
        y -= 12
    return y
