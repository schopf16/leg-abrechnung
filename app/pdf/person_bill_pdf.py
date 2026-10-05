"""Generates the single combined billing PDF each person receives, per LEG."""

from datetime import date, timedelta
from decimal import Decimal

from app.domain.distribution import PersonQuarterResult
from app.domain.period import quarter_bounds, quarter_label
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.person import Person
from app.models.settings import LegSettings
from app.domain.salutation import letter_salutation
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
    """Format a quarter's date range for display, e.g. "01.07.2026 – 30.09.2026"."""
    start, end = quarter_bounds(year, quarter)
    last_day = end.date() - timedelta(days=1)
    return f"{start.strftime('%d.%m.%Y')} – {last_day.strftime('%d.%m.%Y')}"


def _energy_lines(breakdown: BillBreakdown, price_rp_per_kwh: float) -> list[TableLine]:
    """Turn a grouped breakdown into the document's energy lines."""
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
    """Render one person's combined billing document as a PDF."""
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
    y = draw_intro_text(canvas, letter_salutation(person), y)
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
