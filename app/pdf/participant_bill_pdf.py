"""Generates the single combined billing PDF each participant receives.

Every participant gets exactly one document, regardless of whether they
only consume, only produce, or both (project brief follow-up: "jede
Partei erhält nur 1 PDF"). The document shows, in order:

1. Bezug (consumption), broken down by calendar month, with a subtotal.
2. Vergütung (production), broken down by calendar month, with a subtotal.
3. The net settlement: consumption value minus production value, rounded
   to the nearest Rappen exactly once (see `app.domain.billing`).

A Swiss QR-bill is always printed. If the net settlement is a credit (the
LEG owes the participant, `net_amount_rappen <= 0`), the QR-bill carries
no fixed amount and its amount fields are visibly overprinted with
"***.**" so it can never be used to actually transfer money -- the LEG
pays the participant directly (see the payment list), the participant
never pays via this document in that case.
"""

from datetime import date
from decimal import Decimal

from app.domain.distribution import ParticipantQuarterResult
from app.domain.period import month_label_de, months_in_quarter, quarter_label
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.participant import Participant
from app.models.settings import LegSettings
from app.pdf.layout import (
    CONTENT_BOTTOM_Y,
    draw_intro_text,
    draw_meta_block,
    draw_monthly_table,
    draw_net_settlement,
    draw_recipient_block,
    draw_sender_block,
    draw_title,
    new_canvas,
)
from app.pdf.qr_bill_render import (
    build_qr_bill,
    draw_qr_bill,
    draw_void_amount_overlay,
)
from app.pdf.qr_reference import generate_qrr_reference


def _monthly_rows(
    year: int, quarter: int, kwh_by_month: dict[int, float], price_rp_per_kwh: float
) -> tuple[list[tuple[str, str, str, str]], float]:
    """Build display rows and an unrounded subtotal for one monthly table.

    Args:
        year: Calendar year of the billing quarter.
        quarter: Quarter number, 1 to 4.
        kwh_by_month: Energy per calendar month, keyed by month number.
        price_rp_per_kwh: Internal energy price in Rappen per kWh.

    Returns:
        A `(rows, subtotal_chf)` tuple: `rows` are
        `(month_label, kwh_text, price_text, amount_text)` tuples in
        chronological order; `subtotal_chf` is the unrounded sum of the
        rows' amounts, in Swiss francs, for display only.
    """
    rows = []
    subtotal_rappen = 0.0
    for _, month in months_in_quarter(year, quarter):
        kwh = kwh_by_month.get(month, 0.0)
        amount_rappen = kwh * price_rp_per_kwh
        subtotal_rappen += amount_rappen
        rows.append(
            (
                month_label_de(year, month),
                f"{kwh:.3f}",
                f"{price_rp_per_kwh:.2f}",
                f"{amount_rappen / 100:.2f}",
            )
        )
    return rows, subtotal_rappen / 100


def generate_participant_bill_pdf(
    run: BillingRun,
    item: BillingRunItem,
    participant_result: ParticipantQuarterResult,
    participant: Participant,
    settings: LegSettings,
    output_path,
):
    """Render one participant's combined billing document as a PDF.

    Args:
        run: The billing run the item belongs to.
        item: The participant's netted billing item (provides the
            authoritative, already-rounded `net_amount_rappen` used for
            the QR-bill and payment list).
        participant_result: The same participant's distribution result for
            the quarter, providing the monthly Bezug/Vergütung breakdown
            shown in the document's tables.
        participant: The participant this document is addressed to.
        settings: Current LEG settings (sender, QR-IBAN).
        output_path: Destination path for the generated PDF.

    Returns:
        `output_path`, for convenience.

    Raises:
        app.pdf.qr_bill_render.QrBillConfigurationError: If the LEG
            settings are missing required fields for a valid QR-bill.
    """
    period = quarter_label(run.period_year, run.period_quarter)
    canvas = new_canvas(output_path)

    draw_sender_block(canvas, settings)
    draw_recipient_block(canvas, participant)
    draw_meta_block(
        canvas,
        [
            f"Abrechnung Nr. {item.id}",
            f"Datum: {date.today().strftime('%d.%m.%Y')}",
            f"Periode: {period}",
        ],
    )

    y = draw_title(canvas, "Abrechnung")
    y = draw_intro_text(
        canvas,
        f"Abrechnung des lokal geteilten Stroms Ihrer Energiegemeinschaft für {period}.",
        y,
    )

    if item.consumed_kwh > 0:
        rows, consumed_subtotal_chf = _monthly_rows(
            run.period_year, run.period_quarter, participant_result.consumed_by_month, item.price_rp_per_kwh
        )
        y = draw_monthly_table(
            canvas,
            y,
            "Bezug (lokal gedeckter Verbrauch)",
            rows,
            "Zwischensumme Bezug",
            f"{consumed_subtotal_chf:.2f} CHF",
        )

    if item.produced_kwh > 0:
        rows, produced_subtotal_chf = _monthly_rows(
            run.period_year, run.period_quarter, participant_result.produced_by_month, item.price_rp_per_kwh
        )
        y = draw_monthly_table(
            canvas,
            y,
            "Vergütung (lokal gelieferte Produktion)",
            rows,
            "Zwischensumme Vergütung",
            f"{produced_subtotal_chf:.2f} CHF",
        )

    net_amount_chf = Decimal(item.net_amount_rappen) / 100
    if item.is_owed_to_leg:
        note = (
            "Bitte begleichen Sie diesen Betrag mit dem beiliegenden Einzahlungsschein."
        )
    elif item.is_owed_by_leg:
        note = (
            "Dieser Betrag wird Ihnen von der Energiegemeinschaft überwiesen.\n"
            "Der beiliegende Einzahlungsschein ist absichtlich entwertet "
            "(Betrag ***.**) und darf nicht für eine Zahlung verwendet werden."
        )
    else:
        note = "Für diese Periode ist kein Betrag fällig."

    y = draw_net_settlement(
        canvas,
        y,
        "Netto-Betrag (keine MWST)",
        f"{net_amount_chf:.2f} CHF",
        note,
    )

    # The QR-bill always occupies the bottom 106mm of whatever page it is
    # drawn on. If the content above would run into that reserved zone
    # (e.g. a prosumer with both a Bezug and a Vergütung table), start a
    # fresh page for the QR-bill instead of letting the two collide.
    if y < CONTENT_BOTTOM_Y:
        canvas.showPage()

    reference = generate_qrr_reference(participant.id, run.id, item.id)
    payable_amount = net_amount_chf if item.is_owed_to_leg else None
    bill = build_qr_bill(settings, participant, payable_amount, reference)
    draw_qr_bill(canvas, bill)
    if payable_amount is None:
        draw_void_amount_overlay(canvas)

    canvas.showPage()
    canvas.save()
    return output_path
