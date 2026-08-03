"""Shared A4 letterhead drawing helpers for invoices and credit notes.

Both document types share the same sender/recipient block, title and
line-item table; only the payment section below differs (Swiss QR-bill for
invoices, plain IBAN note for credit notes -- see `app.pdf.invoice_pdf` and
`app.pdf.credit_pdf`).
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from app.models.participant import Participant
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


def draw_sender_block(canvas: Canvas, settings: LegSettings) -> None:
    """Draw the LEG's sender address in the top-left corner.

    Args:
        canvas: Target canvas.
        settings: LEG settings providing name and address.

    Returns:
        None.
    """
    y = PAGE_HEIGHT - 20 * mm
    canvas.setFont("Helvetica", 8)
    for line in (
        settings.name,
        settings.address_street,
        f"{settings.address_zip} {settings.address_city}",
    ):
        canvas.drawString(_LEFT_MARGIN, y, line)
        y -= 10


def draw_recipient_block(canvas: Canvas, participant: Participant) -> None:
    """Draw the recipient's address, positioned for a windowed envelope.

    Args:
        canvas: Target canvas.
        participant: Recipient of the document.

    Returns:
        None.
    """
    y = PAGE_HEIGHT - 55 * mm
    canvas.setFont("Helvetica", 10)
    for line in (
        participant.name,
        participant.address_street,
        f"{participant.address_zip} {participant.address_city}",
    ):
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


def draw_items_table(
    canvas: Canvas,
    top_y: float,
    rows: list[tuple[str, str, str]],
    total_label: str,
    total_value: str,
) -> float:
    """Draw a simple three-column line-item table with a total row.

    Args:
        canvas: Target canvas.
        top_y: Y-coordinate (points from page bottom) of the table's top edge.
        rows: `(description, quantity_and_price, amount)` tuples.
        total_label: Label for the total row, e.g. "Total (keine MWST)".
        total_value: Formatted total amount, e.g. "123.45 CHF".

    Returns:
        The y-coordinate directly below the table.
    """
    col_description_x = _LEFT_MARGIN
    col_quantity_x = PAGE_WIDTH - 80 * mm
    col_amount_x = PAGE_WIDTH - _RIGHT_MARGIN

    y = top_y
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(col_description_x, y, "Position")
    canvas.drawString(col_quantity_x, y, "Menge / Preis")
    canvas.drawRightString(col_amount_x, y, "Betrag")
    y -= 6
    canvas.line(_LEFT_MARGIN, y, PAGE_WIDTH - _RIGHT_MARGIN, y)
    y -= 12

    canvas.setFont("Helvetica", 9)
    for description, quantity, amount in rows:
        canvas.drawString(col_description_x, y, description)
        canvas.drawString(col_quantity_x, y, quantity)
        canvas.drawRightString(col_amount_x, y, amount)
        y -= 14

    y -= 4
    canvas.line(_LEFT_MARGIN, y, PAGE_WIDTH - _RIGHT_MARGIN, y)
    y -= 14
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(col_description_x, y, total_label)
    canvas.drawRightString(col_amount_x, y, total_value)
    return y - 10 * mm
