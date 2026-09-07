"""Builds the chronological ledger shown in a Person's Debitoren detail
view: every invoice/credit issued, every Mahnung sent, and every payment/
payout/correction booked, merged into one timeline that explains how the
current Saldo actually came about -- not just the final number.

Sign convention: `amount_rappen` on a movement entry (`rechnung`,
`gutschrift`, `zahlungseingang`, `auszahlung`, `korrektur`) uses the exact
same internal convention as `BillingRunItem.net_amount_rappen`/
`AccountEntry.amount_rappen` (positive = increases what the person owes
the LEG) -- no second convention invented here, so the GUI's existing
single negate-for-display rule (see `app.gui.pages.debitoren`) applies
identically to every line, not just the aggregate Saldo. A `mahnung` entry
is purely informational (sending a Mahnung does not itself move the
Saldo), so its `amount_rappen` is always `None`.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.models import account_entry as account_entry_repo
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import mahnung_log as mahnung_log_repo
from app.pdf.person_bill_pdf import PAYMENT_TERM


@dataclass
class LedgerEntry:
    """One event in a person's Debitoren history.

    Attributes:
        entry_date: ISO date this event is dated to, for sorting/display.
        kind: `"rechnung"`, `"gutschrift"`, `"mahnung"`, `"zahlungseingang"`,
            `"auszahlung"` or `"korrektur"`.
        description: Human-readable (German) summary of this event.
        amount_rappen: Signed amount in internal convention (see module
            docstring), or `None` for a purely informational entry (a
            `mahnung` send -- it does not move the Saldo by itself).
        billing_run_item_id: The invoice/credit this event is about or
            refers to, if any -- lets the GUI offer "Details ansehen"
            (see `app.gui.invoice_detail`) directly from this line.
    """

    entry_date: str
    kind: str
    description: str
    amount_rappen: Optional[int]
    billing_run_item_id: Optional[int] = None


_ACCOUNT_ENTRY_LABELS = {
    "zahlungseingang": "Zahlungseingang",
    "auszahlung": "Auszahlung",
    "korrektur": "Korrektur",
}


def list_ledger_entries(connection, person_id: int) -> list[LedgerEntry]:
    """Build one person's full Debitoren timeline, oldest first.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.

    Returns:
        Every invoice/credit, Mahnung and payment/payout/correction
        involving this person, sorted by `entry_date` (ties broken in
        insertion order: invoices, then Mahnungen, then account entries).
    """
    entries: list[LedgerEntry] = []

    for item in billing_run_repo.list_items_for_person(connection, person_id):
        run = billing_run_repo.get_run(connection, item.billing_run_id)
        leg = leg_repo.get(connection, run.leg_id) if run else None
        leg_name = leg.name if leg else "?"
        period = f"Q{run.period_quarter}/{run.period_year}" if run else "?"

        if item.faellig_am:
            issue_date = (date.fromisoformat(item.faellig_am) - PAYMENT_TERM).isoformat()
        else:
            issue_date = item.created_at[:10]

        if item.is_owed_to_leg:
            kind, label = "rechnung", "Rechnung gestellt"
        elif item.is_owed_by_leg:
            kind, label = "gutschrift", "Gutschrift erstellt"
        else:
            kind, label = "rechnung", "Abrechnung (kein Saldo)"

        entries.append(
            LedgerEntry(
                entry_date=issue_date,
                kind=kind,
                description=f"{label}: {leg_name}, {period}",
                amount_rappen=item.net_amount_rappen,
                billing_run_item_id=item.id,
            )
        )

    for log in mahnung_log_repo.list_for_person(connection, person_id):
        entries.append(
            LedgerEntry(
                entry_date=log.sent_at[:10],
                kind="mahnung",
                description=(
                    f"{log.stufe}. Mahnung gesendet "
                    f"({len(log.billing_run_item_ids)} Position(en), "
                    f"CHF {log.betrag_rappen / 100:.2f} offen)"
                ),
                amount_rappen=None,
            )
        )

    for entry in account_entry_repo.list_for_person(connection, person_id):
        label = _ACCOUNT_ENTRY_LABELS.get(entry.kind, entry.kind)
        description = f"{label} ({entry.note})" if entry.note else label
        entries.append(
            LedgerEntry(
                entry_date=entry.booked_at[:10],
                kind=entry.kind,
                description=description,
                amount_rappen=entry.amount_rappen,
                billing_run_item_id=entry.billing_run_item_id,
            )
        )

    entries.sort(key=lambda e: e.entry_date)
    return entries
