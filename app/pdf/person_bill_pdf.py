"""Generates the single combined billing PDF each person receives, per LEG.

Every person gets exactly one document per LEG they participate in for a
quarter, regardless of whether they only consume, only produce, or both
(project brief follow-up: "jede Partei erhält nur 1 PDF"). That holds
however many sites and metering points they hold: a participant is one
customer of the LEG with one netted amount, the way a vZEV operator
receives one figure from the grid operator and works out the internal
shares themselves. The document shows, in order:

1. The locally shared energy, grouped by site: each site's address, its
   Bezug and its Einspeisung itemised per metering point, and that
   site's own balance -- the figure a participant with several sites
   carries into their own internal allocation.
2. admin fee (admin surcharge on consumption) and Kosten
   paper invoice (flat paper-invoice fee), if either applies.
3. The net settlement: consumption value minus production value plus the
   two fees above, rounded to the nearest Rappen exactly once for the
   energy portion (the fees are their own already-rounded/exact lines --
   see `app.domain.billing`'s module docstring).

Every franc figure above the net settlement is an unrounded display
value; the grouping itself is `app.pdf.bill_breakdown`, kept separate so
it can be tested without generating a PDF.

A Swiss QR-bill (Einzahlungsschein) is only printed when the person
actually owes the LEG money (`net_amount_rappen > 0`). When the net
settlement is a credit or zero (the LEG owes the person, or nothing is
due), there is nothing to pay via a payment slip -- the LEG pays the
person directly (see the payout list) -- so the whole QR-bill section,
and the extra page it would otherwise need, is omitted entirely.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.domain.distribution import PersonQuarterResult
from app.domain.period import quarter_bounds, quarter_label
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.person import Person
from app.models.settings import LegSettings
from app.pdf.bill_breakdown import BillBreakdown, MeteringPointInfo, build_bill_breakdown
from app.pdf.layout import (
    CONTENT_BOTTOM_Y,
    TableLine,
    draw_billing_table,
    draw_intro_text,
    draw_meta_block,
    draw_net_settlement,
    draw_recipient_block,
    draw_sender_block,
    draw_title,
    ensure_space,
    new_canvas,
)
from app.pdf.qr_bill_render import build_qr_bill, draw_qr_bill
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


def _energy_lines(breakdown: BillBreakdown, price_rp_per_kwh: float) -> list[TableLine]:
    """Turn a grouped breakdown into the document's energy lines.

    One block per site -- heading, a "Bezug" section, an "Einspeisung"
    section, and the site's own balance. The balance is the figure a
    participant with several sites carries into their own internal
    allocation; the LEG itself only ever settles the single net amount
    below.

    The grand totals per direction are only appended when the document
    covers more than one site: with a single site they would repeat the
    block's own balance one line further down.

    Args:
        breakdown: The person's quarter, grouped by site.
        price_rp_per_kwh: The run's frozen price, in Rappen per kWh.

    Returns:
        The lines to hand to `draw_billing_table`.
    """
    price_text = f"{price_rp_per_kwh:.2f}"
    lines: list[TableLine] = []

    for site in breakdown.sites:
        lines.append(TableLine(site.address, style="group"))
        for section_label, rows in (("Bezug", site.consumption), ("Einspeisung", site.feed_in)):
            if not rows:
                continue
            lines.append(TableLine(section_label, style="subgroup"))
            lines.extend(
                TableLine(row.name, f"{row.kwh:.3f}", price_text, f"{row.amount_chf:.2f}") for row in rows
            )
        lines.append(TableLine("Saldo Standort", amount=f"{site.balance_chf:.2f}", style="total"))

    if breakdown.has_multiple_sites:
        lines.append(
            TableLine(
                "Total Bezug",
                f"{breakdown.total_consumed_kwh:.3f}",
                amount=f"{breakdown.total_consumption_chf:.2f}",
                style="total",
            )
        )
        if breakdown.feed_in_rows:
            lines.append(
                TableLine(
                    "Total Einspeisung",
                    f"{breakdown.total_produced_kwh:.3f}",
                    amount=f"{breakdown.total_feed_in_chf:.2f}",
                    style="total",
                )
            )

    return lines


def generate_person_bill_pdf(
    run: BillingRun,
    item: BillingRunItem,
    person_result: PersonQuarterResult,
    person: Person,
    leg: Leg,
    settings: LegSettings,
    output_path,
    *,
    metering_point_info: dict[int, MeteringPointInfo],
):
    """Render one person's combined billing document as a PDF.

    Args:
        run: The billing run the item belongs to (scoped to one LEG).
        item: The person's netted billing item (provides the
            authoritative, already-rounded `net_amount_rappen`,
            `admin_fee_consumption_rappen`/
            `admin_fee_feed_in_rappen` and
            `paper_invoice_rappen` used for the QR-bill and payment list).
            `item.due_date` must already be resolved by the caller
            (see `app.pdf.export_service.export_billing_run`) -- printed
            verbatim here, never recomputed, so a re-export can never
            print a due date that drifts from the one already frozen in
            the database and used by `app.domain.dunning`.
        person_result: The same person's distribution result for the
            quarter, providing the quarter's consumption/Vergütung totals shown
            in the document's tables.
        person: The person this document is addressed to.
        leg: The LEG this document is billed under (provides the
            letterhead name).
        settings: Current LEG-wide settings (address, QR-IBAN, admin fee
            rate for display).
        output_path: Destination path for the generated PDF.
        metering_point_info: Resolved metering point and site data for
            every metering point in `person_result`, keyed by metering
            point id (built by the caller, see
            `app.pdf.export_service.export_billing_run_documents`) --
            this layer reads no database.

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
            f"Zahlbar bis: {date.fromisoformat(item.due_date).strftime('%d.%m.%Y')}",
            f"Kunden-Nr.: {person.formatted_customer_number}",
            f"Periode: {period}",
            _quarter_period_label(run.period_year, run.period_quarter),
        ],
    )

    y = draw_title(canvas, "Abrechnung")
    y = draw_intro_text(canvas, "Sehr geehrte Kundin, sehr geehrter Kunde", y)
    y = draw_intro_text(
        canvas,
        f"Sie erhalten nachfolgend die Abrechnung des lokal geteilten Stroms für {period}.",
        y,
    )

    breakdown = build_bill_breakdown(person_result, metering_point_info, item.price_rp_per_kwh)
    energy_lines = _energy_lines(breakdown, item.price_rp_per_kwh)
    if energy_lines:
        y = draw_billing_table(
            canvas,
            y,
            "Lokal geteilter Strom",
            energy_lines,
            label_header="Standort / Messpunkt",
        )

    if (
        item.admin_fee_consumption_rappen > 0
        or item.admin_fee_feed_in_rappen > 0
        or item.paper_invoice_rappen > 0
    ):
        # Rates read from the item itself, never from `settings` -- these
        # are frozen at billing time, so a later rate change in
        # Einstellungen never alters how an already-billed fee appears
        # (see the module docstring of app.domain.billing).
        fee_lines = []
        if item.admin_fee_consumption_rappen > 0:
            fee_lines.append(
                TableLine(
                    "Verwaltungsaufwand Bezug",
                    f"{item.consumed_kwh:.3f}",
                    f"{item.admin_fee_consumption_rp_per_kwh:.4f}",
                    f"{item.admin_fee_consumption_rappen / 100:.2f}",
                )
            )
        if item.admin_fee_feed_in_rappen > 0:
            fee_lines.append(
                TableLine(
                    "Verwaltungsaufwand Einspeisung",
                    f"{item.produced_kwh:.3f}",
                    f"{item.admin_fee_feed_in_rp_per_kwh:.4f}",
                    f"{item.admin_fee_feed_in_rappen / 100:.2f}",
                )
            )
        fee_lines.append(TableLine("Kosten Papierrechnung", amount=f"{item.paper_invoice_rappen / 100:.2f}"))
        fee_total_chf = (
            item.admin_fee_consumption_rappen + item.admin_fee_feed_in_rappen + item.paper_invoice_rappen
        ) / 100
        fee_lines.append(TableLine("Total Verwaltungsaufwand", amount=f"{fee_total_chf:.2f}", style="total"))
        y = draw_billing_table(canvas, y, "Verwaltungsaufwand", fee_lines, label_header="Position")

    # The net settlement is drawn as one block and cannot break, so give it
    # a page of its own rather than let it run off the bottom of a long
    # itemisation.
    y = ensure_space(canvas, y, 45)

    net_amount_chf = Decimal(item.net_amount_rappen) / 100
    if item.is_owed_to_leg:
        note = "Bitte begleichen Sie diesen Betrag mit dem beiliegenden Einzahlungsschein."
    elif item.is_owed_by_leg:
        note = "Dieser Betrag wird Ihnen von der Energiegemeinschaft überwiesen."
    else:
        note = "Für diese Periode ist kein Betrag fällig."

    y = draw_net_settlement(
        canvas,
        y,
        "Netto-Betrag (keine MWST)",
        f"{net_amount_chf:.2f} CHF",
        note,
    )

    # The Einzahlungsschein is only meaningful when the person actually
    # owes the LEG money -- a credit or zero balance is settled directly
    # by the LEG (see the payout list), so there is nothing to pay via a
    # payment slip and the whole QR-bill section (and the extra page it
    # would otherwise force) is skipped entirely.
    if item.is_owed_to_leg:
        # The QR-bill always occupies the bottom 106mm of whatever page it
        # is drawn on. If the content above would run into that reserved
        # zone (e.g. a prosumer with both a consumption and a Vergütung table),
        # start a fresh page for the QR-bill instead of letting the two
        # collide.
        if y < CONTENT_BOTTOM_Y:
            canvas.showPage()

        reference = generate_qrr_reference(person.customer_number, run.id, item.id)
        bill = build_qr_bill(settings, leg, person, net_amount_chf, reference)
        draw_qr_bill(canvas, bill)

    canvas.showPage()
    canvas.save()
    return output_path
