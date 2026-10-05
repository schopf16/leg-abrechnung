"""Shared A4 letterhead and table drawing helpers for the combined per-person billing document (see
`app.pdf.person_bill_pdf`)."""

from dataclasses import dataclass

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
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
    """Create a new A4 PDF canvas at the given filesystem path."""
    return Canvas(str(path), pagesize=A4)


def draw_sender_block(canvas: Canvas, settings: LegSettings, leg: Leg) -> None:
    """Draw the sender address in the top-left corner."""
    y = PAGE_HEIGHT - 20 * mm
    canvas.setFont("Helvetica", 8)
    for line in (
        leg.name,
        settings.address_street_with_number,
        f"{settings.address_zip} {settings.address_city}",
    ):
        canvas.drawString(_LEFT_MARGIN, y, line)
        y -= 10


def draw_recipient_block(canvas: Canvas, person: Person) -> None:
    """Draw the recipient's billing address, positioned for a windowed envelope."""
    y = PAGE_HEIGHT - 55 * mm
    canvas.setFont("Helvetica", 10)
    lines = [
        *person.address_block_lines,
        person.billing_street_with_number,
        f"{person.billing_postal_code} {person.billing_city}",
    ]
    for line in lines:
        if line.strip():
            canvas.drawString(_LEFT_MARGIN, y, line)
            y -= 12


def draw_meta_block(canvas: Canvas, lines: list[str]) -> None:
    """Draw a right-aligned metadata block (document number, date, period)."""
    y = PAGE_HEIGHT - 20 * mm
    canvas.setFont("Helvetica", 9)
    for line in lines:
        canvas.drawRightString(PAGE_WIDTH - _RIGHT_MARGIN, y, line)
        y -= 12


def draw_title(canvas: Canvas, title: str, y_mm_from_top: float = 90) -> float:
    """Draw the document title (e.g. "Rechnung" or "credit note")."""
    y = PAGE_HEIGHT - y_mm_from_top * mm
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(_LEFT_MARGIN, y, title)
    return y - 10 * mm


def draw_intro_text(canvas: Canvas, text: str, top_y: float) -> float:
    """Draw a paragraph of intro text below the title."""
    canvas.setFont("Helvetica", 10)
    canvas.drawString(_LEFT_MARGIN, top_y, text)
    return top_y - 10 * mm


#: Y-coordinate a continuation page starts its content at. Such a page
#: carries no letterhead or recipient block, so it starts higher than
#: page one does.
CONTINUATION_TOP_Y = PAGE_HEIGHT - 25 * mm

#: Lowest y a table line may be drawn at: the page's bottom margin, *not*
#: `CONTENT_BOTTOM_Y`. Keeping tables out of the QR-bill's area would cost
#: a whole page on the most ordinary bill there is -- one metering point
#: and a fee line -- for a payment slip that is then placed on the next
#: page anyway. Which page the QR-bill lands on is decided once, by the
#: caller, after all content is drawn (see `app.pdf.person_bill_pdf`).
TABLE_BOTTOM_Y = 25 * mm

_COL_KWH_X = PAGE_WIDTH - 95 * mm
_COL_PRICE_X = PAGE_WIDTH - 60 * mm
_COL_AMOUNT_X = PAGE_WIDTH - _RIGHT_MARGIN

#: Vertical space each line style occupies, and how it is drawn: font,
#: size and indent from the left margin. `group` reserves more than it
#: draws, which is the blank line that separates one site from the next.
_LINE_STYLES = {
    "group": ("Helvetica-Bold", 10, 0, 22),
    "subgroup": ("Helvetica-Oblique", 9, 8, 13),
    "row": ("Helvetica", 9, 18, 13),
    "total": ("Helvetica-Bold", 9, 8, 15),
}

#: Blank space above a group heading, separating it from the block before
#: it. Not applied to the first group, which already sits below the
#: column rule -- a leading gap there buys nothing and costs the most
#: ordinary bill (one site, two metering points) a second page.
_GROUP_LEAD = 9


@dataclass(frozen=True)
class TableLine:
    """One drawable line of a billing table."""

    label: str
    kwh: str = ""
    price: str = ""
    amount: str = ""
    style: str = "row"


#: Space kept clear between the left-hand label and the kWh column, wide
#: enough for the longest quantity this app prints.
_LABEL_GUTTER = 45


def _fit(text: str, font: str, size: float, max_width: float) -> str:
    """Shorten text with an ellipsis until it fits a given width."""
    if stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = "…"
    shortened = text
    while shortened and stringWidth(shortened + ellipsis, font, size) > max_width:
        shortened = shortened[:-1]
    return (shortened.rstrip() + ellipsis) if shortened else ellipsis


def _draw_table_header(canvas: Canvas, y: float, section_title: str, label_header: str) -> float:
    """Draw a table's title and column headers."""
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(_LEFT_MARGIN, y, section_title)
    y -= 8 * mm

    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(_LEFT_MARGIN, y, label_header)
    canvas.drawRightString(_COL_KWH_X, y, "kWh")
    canvas.drawRightString(_COL_PRICE_X, y, "Rp./kWh")
    canvas.drawRightString(_COL_AMOUNT_X, y, "Betrag (CHF)")
    y -= 6
    canvas.line(_LEFT_MARGIN, y, PAGE_WIDTH - _RIGHT_MARGIN, y)
    return y - 12


def draw_billing_table(
    canvas: Canvas,
    top_y: float,
    section_title: str,
    lines: list[TableLine],
    *,
    label_header: str = "Position",
) -> float:
    """Draw a billing table, breaking onto further pages when it runs long."""
    y = _draw_table_header(canvas, top_y, section_title, label_header)
    current_group: "TableLine | None" = None
    current_subgroup: "TableLine | None" = None

    for index, line in enumerate(lines):
        font, size, indent, height = _LINE_STYLES[line.style]
        # A group's height includes its lead-in gap, which the first group
        # of a table does not get: directly under the column rule it buys
        # nothing, and it costs the most ordinary bill there is -- one
        # site, two metering points -- a whole second page.
        lead = _GROUP_LEAD if line.style == "group" and index > 0 else 0
        needed = (height - _GROUP_LEAD + lead) if line.style == "group" else height
        if line.style in ("group", "subgroup") and index + 1 < len(lines):
            needed += _LINE_STYLES[lines[index + 1].style][3]
        if y - needed < TABLE_BOTTOM_Y:
            canvas.showPage()
            y = _draw_table_header(canvas, CONTINUATION_TOP_Y, f"{section_title} (Fortsetzung)", label_header)
            # Carry the site (and the Bezug/Einspeisung section) over to
            # the new page. Without this, a participant with enough
            # metering points to fill a page finds the rest of them
            # listed under no address at all -- which is precisely the
            # reader this itemisation exists for.
            if line.style not in ("group", "subgroup"):
                for carried in (current_group, current_subgroup):
                    if carried is None:
                        continue
                    c_font, c_size, c_indent, c_height = _LINE_STYLES[carried.style]
                    canvas.setFont(c_font, c_size)
                    canvas.drawString(
                        _LEFT_MARGIN + c_indent,
                        y,
                        _fit(
                            f"{carried.label} (Fortsetzung)",
                            c_font,
                            c_size,
                            _COL_KWH_X - _LABEL_GUTTER - _LEFT_MARGIN - c_indent,
                        ),
                    )
                    y -= c_height - (_GROUP_LEAD if carried.style == "group" else 0)

        if line.style == "group":
            current_group, current_subgroup = line, None
        elif line.style == "subgroup":
            current_subgroup = line
        elif line.style == "total":
            current_group, current_subgroup = None, None

        y -= lead
        canvas.setFont(font, size)
        canvas.drawString(
            _LEFT_MARGIN + indent,
            y,
            _fit(line.label, font, size, _COL_KWH_X - _LABEL_GUTTER - _LEFT_MARGIN - indent),
        )
        if line.kwh:
            canvas.drawRightString(_COL_KWH_X, y, line.kwh)
        if line.price:
            canvas.drawRightString(_COL_PRICE_X, y, line.price)
        if line.amount:
            canvas.drawRightString(_COL_AMOUNT_X, y, line.amount)
        y -= height - _GROUP_LEAD if line.style == "group" else height

    if y - 8 < TABLE_BOTTOM_Y:
        canvas.showPage()
        y = CONTINUATION_TOP_Y
    y -= 4
    canvas.line(_LEFT_MARGIN, y, PAGE_WIDTH - _RIGHT_MARGIN, y)
    return y - 8 * mm


def ensure_space(canvas: Canvas, y: float, needed_mm: float) -> float:
    """Start a new page if the next block would not fit on this one."""
    if y - needed_mm * mm < TABLE_BOTTOM_Y:
        canvas.showPage()
        return CONTINUATION_TOP_Y
    return y


def draw_net_settlement(canvas: Canvas, top_y: float, label: str, value: str, note: str) -> float:
    """Draw the final, rounded net settlement line and an explanatory note."""
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
