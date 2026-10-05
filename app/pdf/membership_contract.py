"""The Beitrittserklärung, filled in: page 1 drawn, the rest appended.

The official document (`LEG-Ittigen-Beitrittserklaerung-und-Gesellschafts⁠
vertrag.pdf`, seven pages) is a **static** PDF with no form fields, so there
is nothing to fill in the usual sense. Three ways were weighed and the
administrator chose this one:

1. Stamp the values onto the original's page 1 at fixed coordinates. Looks
   exactly like the official sheet, logo and all -- and breaks the moment
   anybody re-lays-out the document, which is a Word file on a website this
   app does not control.
2. **Draw page 1 here and append pages 2 onward unchanged.** No coordinates
   to drift. Page 1 does not look pixel-for-pixel like the original, so it
   carries the original's wording verbatim instead -- every label, in order
   -- which is what makes it recognisable as the same form.
3. Two separate attachments. No PDF library needed at all, but the recipient
   gets two files and has to work out which one to sign.

`reportlab` can create a PDF and not read one, so appending needs `pypdf`
(BSD-3-Clause, pure Python, no dependencies of its own on 3.11+).

**Three lines stay empty on purpose**: Ort, Datum and the signature. They
come from the participant, with a pen, and filling them in would be this
app asserting something it cannot know.
"""

from pathlib import Path
from typing import Optional

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from app.domain.membership_contract import ContractFields

PAGE_WIDTH, PAGE_HEIGHT = A4

_LEFT = 25 * mm
#: Where the values start. Far enough right that the longest label
#: ("Messpunktnummer Einspeisung:") still fits in front of it.
_VALUE_LEFT = 95 * mm
_RIGHT = PAGE_WIDTH - 20 * mm
_LINE = 6.5 * mm

#: The form's own wording, in the form's own order. Copied verbatim from
#: page 1 of the official document: a recipient who has seen that sheet has
#: to recognise this one, and the labels are the only part of it this page
#: reproduces.
_TITLE = "Beitrittserklärung zur Gesellschaft «LEG-Ittigen»"
_INTRO = (
    "Mit Ihren Angaben und Ihrer Unterschrift melden Sie sich verbindlich zur "
    "Teilnahme an und akzeptieren den untenstehenden Gesellschaftsvertrag "
    "«LEG-Ittigen»."
)


def _draw_wrapped(canvas: Canvas, text: str, top: float) -> float:
    """Draw a paragraph, breaking it at the right margin.

    Args:
        canvas: The canvas to draw on.
        text: The paragraph.
        top: Baseline of the first line.

    Returns:
        The baseline below the last line drawn.
    """
    canvas.setFont("Helvetica", 9)
    words, line, y = text.split(), "", top
    for word in words:
        candidate = f"{line} {word}".strip()
        if canvas.stringWidth(candidate, "Helvetica", 9) > _RIGHT - _LEFT:
            canvas.drawString(_LEFT, y, line)
            y -= _LINE * 0.75
            line = word
        else:
            line = candidate
    if line:
        canvas.drawString(_LEFT, y, line)
    return y - _LINE


def _draw_field(canvas: Canvas, label: str, value: str, y: float) -> float:
    """Draw one labelled line, with a rule where the value would be written.

    The rule is drawn whether or not there is a value: the original has a
    row of underscores on every line, and an empty line has to look like
    something that *can* be filled in by hand rather than like a mistake.

    Args:
        canvas: The canvas to draw on.
        label: The form's own wording for this field.
        value: What this app knows, possibly `""`.
        y: Baseline.

    Returns:
        The next baseline.
    """
    canvas.setFont("Helvetica", 9)
    canvas.drawString(_LEFT, y, label)
    canvas.setFont("Helvetica-Bold", 9)
    # Truncated rather than overflowing into the margin: reportlab happily
    # draws off the page and says nothing, the same trap
    # `app.pdf.layout` documents for its table labels.
    text = value
    while text and canvas.stringWidth(text, "Helvetica-Bold", 9) > _RIGHT - _VALUE_LEFT:
        text = text[:-1]
    if text != value and text:
        text = text[:-1] + "…"
    canvas.drawString(_VALUE_LEFT, y, text)
    canvas.setLineWidth(0.3)
    canvas.setStrokeGray(0.6)
    canvas.line(_VALUE_LEFT, y - 1.5, _RIGHT, y - 1.5)
    return y - _LINE


def _draw_heading(canvas: Canvas, text: str, y: float) -> float:
    """Draw one of the form's section headings.

    Args:
        canvas: The canvas to draw on.
        text: The heading.
        y: Baseline.

    Returns:
        The next baseline.
    """
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(_LEFT, y, text)
    return y - _LINE


def draw_first_page(canvas: Canvas, fields: ContractFields) -> None:
    """Draw the filled-in Beitrittserklärung onto one page.

    Args:
        canvas: A canvas positioned at a fresh page.
        fields: What to write, from `app.domain.membership_contract.gather`.

    Returns:
        None.
    """
    canvas.setFont("Helvetica-Bold", 13)
    y = PAGE_HEIGHT - 28 * mm
    canvas.drawString(_LEFT, y, _TITLE)
    y -= _LINE * 1.6
    y = _draw_wrapped(canvas, _INTRO, y)

    y = _draw_heading(canvas, "LEG Teilnehmer:", y - _LINE * 0.4)
    y = _draw_field(canvas, "Firma:", fields.company, y)
    y = _draw_field(canvas, "Vorname, Name:", fields.names, y)
    y = _draw_field(canvas, "Adresse:", fields.address, y)
    y = _draw_field(canvas, "PLZ / Ort:", fields.locality, y)
    y = _draw_field(canvas, "E-Mail:", fields.email, y)
    y = _draw_field(canvas, "Tel:", fields.phone, y)

    y = _draw_field(canvas, "Trafokreis TRA", fields.substation_area, y - _LINE * 0.4)

    y = _draw_heading(canvas, "Bezüger", y - _LINE * 0.4)
    # Every metering point gets its own line. The original has one, and 86
    # of 87 participants need one -- but the one who needs two must not
    # lose a meter to the layout.
    for index, designation in enumerate(fields.consumption_designations or [""]):
        label = "Messpunktnummer Bezug:" if index == 0 else ""
        y = _draw_field(canvas, label, designation, y)

    y = _draw_heading(canvas, "Produzent", y - _LINE * 0.4)
    for index, designation in enumerate(fields.feed_in_designations or [""]):
        label = "Messpunktnummer Einspeisung:" if index == 0 else ""
        y = _draw_field(canvas, label, designation, y)

    y = _draw_heading(canvas, "Zusätzliche Angaben bei Einspeisung (optional)", y - _LINE * 0.4)
    y = _draw_field(canvas, "IBAN-Nr. für Rückvergütung:", fields.iban, y)
    y = _draw_field(canvas, "Leistung Solaranlage (in kWp)", fields.pv_capacity, y)
    y = _draw_field(canvas, "Batteriespeicher (in kWh)", fields.battery_capacity, y)
    y = _draw_field(canvas, "Wallbox max. Leistung (in kW)", fields.wallbox_capacity, y)

    # Ort, Datum and the signature: left blank, and the rules are what say
    # they are meant to be written on.
    y -= _LINE * 1.6
    canvas.setLineWidth(0.3)
    canvas.setStrokeGray(0.6)
    half = _LEFT + (_RIGHT - _LEFT) / 2 - 10 * mm
    canvas.line(_LEFT, y, half - 5 * mm, y)
    canvas.line(half + 5 * mm, y, _RIGHT, y)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(_LEFT, y - 4 * mm, "Ort und Datum")
    canvas.drawString(half + 5 * mm, y - 4 * mm, "Unterschrift LEG Teilnehmer")

    canvas.showPage()


def build_contract(
    fields: ContractFields,
    source_pdf: Optional[bytes],
    target: Path,
) -> Path:
    """Write the filled-in Beitrittserklärung, with the contract behind it.

    Args:
        fields: What goes on page 1.
        source_pdf: The stored official document's bytes. Its **first** page
            is replaced by the one drawn here and the rest are appended. When
            `None` -- no form stored under Einstellungen -- only page 1 is
            written, so the result is still a usable sheet and the caller can
            say what is missing.
        target: Where to write. Its parent is created.

    Returns:
        `target`.
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    first_page = target.with_suffix(".page1.pdf")
    canvas = Canvas(str(first_page), pagesize=A4)
    draw_first_page(canvas, fields)
    canvas.save()

    writer = PdfWriter()
    for page in PdfReader(str(first_page)).pages:
        writer.add_page(page)
    if source_pdf:
        source_pages = PdfReader(_as_stream(source_pdf)).pages
        # From the second page on: the first is the blank form this page
        # replaces.
        for page in source_pages[1:]:
            writer.add_page(page)
    with target.open("wb") as handle:
        writer.write(handle)
    first_page.unlink(missing_ok=True)
    return target


def _as_stream(content: bytes):
    """Wrap bytes so `PdfReader` can read them.

    Args:
        content: The stored document.

    Returns:
        A seekable stream.
    """
    from io import BytesIO

    return BytesIO(content)
