"""Shared in-app invoice/credit-note detail view.

Renders every detail that also appears on the generated PDF (see
`app.pdf.person_bill_pdf`), reading it straight from the persisted
`BillingRunItem`/`BillingRun`/`Leg`/`Person` rows -- deliberately never
from the PDF file itself, which may have been moved, renamed, or deleted
since it was generated, and never from the live `LegSettings` either
(the admin-fee rates actually charged are frozen onto the item itself,
see `app.domain.billing`). This is the primary way to inspect a past
invoice from the receivables detail view (see `app.gui.pages.receivables`),
not a fallback for when the PDF is missing.

Reuses `item.consumed_kwh`/`item.produced_kwh` directly rather than
recomputing the distribution engine: `app.domain.billing.
create_or_replace_billing_run` sets those fields from exactly the same
`PersonQuarterResult` totals `app.pdf.person_bill_pdf` prints, so they are
already the authoritative, frozen-at-billing-time figures -- recomputing
them live would risk a mismatch if readings changed since, and could fail
outright (`LegNotAssignedError`) for old data.
"""

from datetime import date, timedelta

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.period import quarter_bounds, quarter_label
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.pdf.person_bill_pdf import PAYMENT_TERM


def _period_range_label(year: int, quarter: int) -> str:
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


def open_invoice_detail(item_id: int) -> None:
    """Open a dialog showing every detail of one billing run item, as it
    would appear on its generated PDF.

    Args:
        item_id: Primary key of the `BillingRunItem` to show.

    Returns:
        None.
    """
    with connection_scope() as connection:
        item = billing_run_repo.get_item(connection, item_id)
        if item is None:
            ui.notify("Diese Abrechnungsposition existiert nicht mehr.", type="negative")
            return
        run = billing_run_repo.get_run(connection, item.billing_run_id)
        leg = leg_repo.get(connection, run.leg_id) if run else None
        person = person_repo.get(connection, item.person_id)

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
        ui.label(f"Abrechnung Nr. {item.id}").classes("text-lg font-bold")
        if run is not None:
            leg_name = leg.name if leg is not None else "?"
            ui.label(
                f"{leg_name} -- {quarter_label(run.period_year, run.period_quarter)} "
                f"({_period_range_label(run.period_year, run.period_quarter)})"
            ).classes("text-caption text-grey-6")

        with ui.row().classes("w-full gap-6 mt-2 flex-wrap"):
            with ui.column().classes("gap-0"):
                if item.due_date:
                    due = date.fromisoformat(item.due_date)
                    ui.label(f"Datum: {(due - PAYMENT_TERM).strftime('%d.%m.%Y')}").classes("text-body2")
                    ui.label(f"Zahlbar bis: {due.strftime('%d.%m.%Y')}").classes("text-body2")
                else:
                    ui.label("Datum: PDF noch nicht erzeugt").classes("text-body2 text-grey-6")
            if person is not None:
                with ui.column().classes("gap-0"):
                    ui.label(f"Kunden-Nr.: {person.formatted_customer_number}").classes("text-body2")
                    ui.label(person.display_name).classes("text-body2")
                    ui.label(
                        f"{person.billing_street_with_number}, "
                        f"{person.billing_postal_code} {person.billing_city}"
                    ).classes("text-caption text-grey-6")

        ui.separator().classes("my-2")

        if item.consumed_kwh > 0:
            amount_chf = item.consumed_kwh * item.price_rp_per_kwh / 100
            ui.label("Bezug (lokal gedeckter Verbrauch)").classes("font-bold mt-2")
            with ui.row().classes("w-full justify-between text-body2"):
                ui.label(f"{item.consumed_kwh:.3f} kWh × {item.price_rp_per_kwh:.2f} Rp./kWh")
                ui.label(f"{amount_chf:.2f} CHF")

        if item.produced_kwh > 0:
            amount_chf = item.produced_kwh * item.price_rp_per_kwh / 100
            ui.label("Vergütung (lokal gelieferte Produktion)").classes("font-bold mt-2")
            with ui.row().classes("w-full justify-between text-body2"):
                ui.label(f"{item.produced_kwh:.3f} kWh × {item.price_rp_per_kwh:.2f} Rp./kWh")
                ui.label(f"{amount_chf:.2f} CHF")

        if (
            item.admin_fee_consumption_rappen > 0
            or item.admin_fee_feed_in_rappen > 0
            or item.paper_invoice_rappen > 0
        ):
            # Rates read from the item itself (frozen at billing time),
            # never from `settings` -- see app.domain.billing's docstring
            # on why a later rate change must never alter how an
            # already-billed fee is displayed.
            ui.label("Verwaltungsaufwand").classes("font-bold mt-2")
            if item.admin_fee_consumption_rappen > 0:
                with ui.row().classes("w-full justify-between text-body2"):
                    ui.label(
                        f"Verwaltungsaufwand Bezug ({item.consumed_kwh:.3f} kWh × "
                        f"{item.admin_fee_consumption_rp_per_kwh:.4f} Rp./kWh)"
                    )
                    ui.label(f"{item.admin_fee_consumption_rappen / 100:.2f} CHF")
            if item.admin_fee_feed_in_rappen > 0:
                with ui.row().classes("w-full justify-between text-body2"):
                    ui.label(
                        f"Verwaltungsaufwand Einspeisung ({item.produced_kwh:.3f} kWh × "
                        f"{item.admin_fee_feed_in_rp_per_kwh:.4f} Rp./kWh)"
                    )
                    ui.label(f"{item.admin_fee_feed_in_rappen / 100:.2f} CHF")
            if item.paper_invoice_rappen > 0:
                with ui.row().classes("w-full justify-between text-body2"):
                    ui.label("Kosten Papierrechnung")
                    ui.label(f"{item.paper_invoice_rappen / 100:.2f} CHF")

        ui.separator().classes("my-2")

        if item.is_owed_to_leg:
            note = "Zahlbar per Einzahlungsschein."
        elif item.is_owed_by_leg:
            note = "Wird von der Energiegemeinschaft überwiesen."
        else:
            note = "Für diese Periode ist kein Betrag fällig."
        with ui.row().classes("w-full justify-between font-bold"):
            ui.label("Netto-Betrag (keine MWST)")
            ui.label(f"{item.net_amount_chf:.2f} CHF")
        ui.label(note).classes("text-caption text-grey-6")

        ui.separator().classes("my-2")
        ui.label(f"PDF erzeugt: {'ja' if item.pdf_path else 'nein'}").classes("text-caption text-grey-6")
        if item.email_sent_at:
            ui.label(f"Per E-Mail versendet am: {item.email_sent_at[:10]}").classes(
                "text-caption text-grey-6"
            )
        if item.dunning_level:
            zusatz = f", zuletzt am {item.last_dunning_at[:10]}" if item.last_dunning_at else ""
            ui.label(f"Mahnstufe {item.dunning_level}{zusatz}").classes("text-caption text-grey-6")

        with ui.row().classes("w-full justify-end mt-4"):
            ui.button("Schliessen", on_click=dialog.close).props("flat")
    dialog.open()
