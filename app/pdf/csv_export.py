"""Generates the two CSV reconciliation lists for a billing run.

Unlike the per-person billing PDFs, these are not documents sent to
anyone -- they exist so the administrator can check off incoming bank
payments against invoices (via the QRR reference number printed on each
person's Einzahlungsschein, see `app.pdf.qr_reference`) and process
outgoing payouts to producers, without hunting through individual PDFs.
"""

import csv
from datetime import date
from pathlib import Path

from app.models.billing_run import BillingRun, BillingRunItem
from app.models.person import Person
from app.pdf.person_bill_pdf import PAYMENT_TERM
from app.pdf.qr_reference import generate_qrr_reference

_DELIMITER = ";"


def generate_invoice_list_csv(
    run: BillingRun,
    items: list[BillingRunItem],
    persons: dict[int, Person],
    output_path: Path,
) -> Path:
    """Write one row per invoice (person owing the LEG money).

    Lists the same QRR reference number printed on each person's
    Einzahlungsschein, so incoming payments on a bank statement can be
    matched back to the right invoice.

    Args:
        run: The billing run to list invoices for.
        items: All line items of the run (only persons owing the LEG
            money, i.e. `net_amount_rappen > 0`, are listed).
        persons: Person lookup by id, for names and Kundennummern.
        output_path: Destination path for the generated CSV.

    Returns:
        `output_path`, for convenience.
    """
    invoice_items = [i for i in items if i.is_owed_to_leg]
    due_date = date.today() + PAYMENT_TERM

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=_DELIMITER)
        writer.writerow(
            ["Abrechnung Nr.", "Kundennummer", "Name", "Referenznummer", "Betrag (CHF)", "Faellig am"]
        )
        for item in invoice_items:
            person = persons.get(item.person_id)
            reference = generate_qrr_reference(item.person_id, run.id, item.id)
            writer.writerow(
                [
                    item.id,
                    person.kundennummer if person else "",
                    person.name if person else f"Person #{item.person_id}",
                    reference,
                    f"{item.net_amount_rappen / 100:.2f}",
                    due_date.strftime("%d.%m.%Y"),
                ]
            )
    return output_path


def generate_payout_list_csv(
    items: list[BillingRunItem],
    persons: dict[int, Person],
    output_path: Path,
) -> Path:
    """Write one row per payout (person the LEG owes money to).

    Args:
        items: All line items of the run (only persons owed money by the
            LEG, i.e. `net_amount_rappen < 0`, are listed).
        persons: Person lookup by id, for names, Kundennummern and IBANs.
        output_path: Destination path for the generated CSV.

    Returns:
        `output_path`, for convenience.
    """
    payout_items = [i for i in items if i.is_owed_by_leg]

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=_DELIMITER)
        writer.writerow(["Kundennummer", "Name", "IBAN", "Betrag (CHF)"])
        for item in payout_items:
            person = persons.get(item.person_id)
            writer.writerow(
                [
                    person.kundennummer if person else "",
                    person.name if person else f"Person #{item.person_id}",
                    person.iban if person else "",
                    f"{-item.net_amount_rappen / 100:.2f}",
                ]
            )
    return output_path
