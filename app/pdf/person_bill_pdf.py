"""Generates the single combined billing PDF each person receives, per LEG.

Every person gets exactly one document per LEG they participate in for a
quarter, regardless of whether they only consume, only produce, or both
(project brief follow-up: "jede Partei erhält nur 1 PDF"). The document
shows, in order:

1. Bezug (consumption) for the whole quarter, as one summed line.
2. Vergütung (production) for the whole quarter, as one summed line.
3. Verwaltungsaufwand (admin surcharge on consumption) and Kosten
   Papierrechnung (flat paper-invoice fee), if either applies.
4. The net settlement: consumption value minus production value plus the
   two fees above, rounded to the nearest Rappen exactly once for the
   energy portion (the fees are their own already-rounded/exact lines --
   see `app.domain.billing`'s module docstring).

A Swiss QR-bill is always printed. If the net settlement is a credit (the
LEG owes the person, `net_amount_rappen <= 0`), the QR-bill carries no
fixed amount and its amount fields are visibly overprinted with "***.**"
so it can never be used to actually transfer money -- the LEG pays the
person directly (see the payment list), the person never pays via this
document in that case.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.domain.distribution import PersonQuarterResult
from app.domain.period import quarter_bounds, quarter_label
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.person import Person
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

#: How many days after the document date payment is due.
PAYMENT_TERM = timedelta(days=45)


def _quarter_period_label(year: int, quarter: int) -> str:
    """Format a quarter's date range for display, e.g. "01.07.2026 – 30.09.2026".

    Args:
        year: Calendar year of the quarter.
        quarter: Quarter number, 1 to 4.

    Returns:
        The quarter's first and last calendar day, German-formatted.
    """
    start, end = quarter_bounds(year, quarter)
    last_day = end.date() - timedelta(days=1)
    return f"{start.strftime('%d.%m.%Y')} – {last_day.strftime('%d.%m.%Y')}"


def _energy_row(
    year: int, quarter: int, kwh: float, price_rp_per_kwh: float
) -> tuple[list[tuple[str, str, str, str]], float]:
    """Build a single summed display row for one quarter's energy total.

    Args:
        year: Calendar year of the billing quarter.
        quarter: Quarter number, 1 to 4.
        kwh: Total energy for the quarter, in kWh.
        price_rp_per_kwh: Internal energy price in Rappen per kWh.

    Returns:
        A `(rows, subtotal_chf)` tuple: `rows` has exactly one
        `(period_label, kwh_text, price_text, amount_text)` tuple;
        `subtotal_chf` is the unrounded amount in Swiss francs, for
        display only.
    """
    amount_rappen = kwh * price_rp_per_kwh
    row = (
        _quarter_period_label(year, quarter),
        f"{kwh:.3f}",
        f"{price_rp_per_kwh:.2f}",
        f"{amount_rappen / 100:.2f}",
    )
    return [row], amount_rappen / 100


def generate_person_bill_pdf(
    run: BillingRun,
    item: BillingRunItem,
    person_result: PersonQuarterResult,
    person: Person,
    leg: Leg,
    settings: LegSettings,
    output_path,
):
    """Render one person's combined billing document as a PDF.

    Args:
        run: The billing run the item belongs to (scoped to one LEG).
        item: The person's netted billing item (provides the
            authoritative, already-rounded `net_amount_rappen`,
            `verwaltungsaufwand_rappen` and `papierrechnung_rappen` used
            for the QR-bill and payment list).
        person_result: The same person's distribution result for the
            quarter, providing the quarter's Bezug/Vergütung totals shown
            in the document's tables.
        person: The person this document is addressed to.
        leg: The LEG this document is billed under (provides the
            letterhead name).
        settings: Current LEG-wide settings (address, QR-IBAN, admin fee
            rate for display).
        output_path: Destination path for the generated PDF.

    Returns:
        `output_path`, for convenience.

    Raises:
        app.pdf.qr_bill_render.QrBillConfigurationError: If the LEG
            settings are missing required fields for a valid QR-bill.
    """
    period = quarter_label(run.period_year, run.period_quarter)
    canvas = new_canvas(output_path)

    draw_sender_block(canvas, settings, leg)
    draw_recipient_block(canvas, person)
    draw_meta_block(
        canvas,
        [
            f"Abrechnung Nr. {item.id}",
            f"Datum: {date.today().strftime('%d.%m.%Y')}",
            f"Zahlbar bis: {(date.today() + PAYMENT_TERM).strftime('%d.%m.%Y')}",
            f"Kunden-Nr.: {person.kundennummer_formatiert}",
            f"Periode: {period}",
        ],
    )

    y = draw_title(canvas, "Abrechnung")
    y = draw_intro_text(canvas, "Sehr geehrte Kundin, sehr geehrter Kunde", y)
    y = draw_intro_text(
        canvas,
        f"Sie erhalten nachfolgend die Abrechnung des lokal geteilten Stroms für {period}.",
        y,
    )

    if item.consumed_kwh > 0:
        rows, consumed_subtotal_chf = _energy_row(
            run.period_year, run.period_quarter, person_result.consumed_local_kwh, item.price_rp_per_kwh
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
        rows, produced_subtotal_chf = _energy_row(
            run.period_year, run.period_quarter, person_result.produced_local_kwh, item.price_rp_per_kwh
        )
        y = draw_monthly_table(
            canvas,
            y,
            "Vergütung (lokal gelieferte Produktion)",
            rows,
            "Zwischensumme Vergütung",
            f"{produced_subtotal_chf:.2f} CHF",
        )

    if item.verwaltungsaufwand_rappen > 0 or item.papierrechnung_rappen > 0:
        fee_rows = [
            (
                "Verwaltungsaufwand",
                f"{item.consumed_kwh:.3f}",
                f"{settings.verwaltungsaufwand_rp_per_kwh:.4f}",
                f"{item.verwaltungsaufwand_rappen / 100:.2f}",
            ),
            (
                "Kosten Papierrechnung",
                "",
                "",
                f"{item.papierrechnung_rappen / 100:.2f}",
            ),
        ]
        fee_total_chf = (item.verwaltungsaufwand_rappen + item.papierrechnung_rappen) / 100
        y = draw_monthly_table(
            canvas,
            y,
            "Verwaltungsaufwand",
            fee_rows,
            "Total Verwaltungsaufwand",
            f"{fee_total_chf:.2f} CHF",
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

    reference = generate_qrr_reference(person.id, run.id, item.id)
    payable_amount = net_amount_chf if item.is_owed_to_leg else None
    bill = build_qr_bill(settings, leg, person, payable_amount, reference)
    draw_qr_bill(canvas, bill)
    if payable_amount is None:
        draw_void_amount_overlay(canvas)

    canvas.showPage()
    canvas.save()
    return output_path
