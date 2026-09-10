"""Per-person account ledger (Debitorenkonto): every bank-confirmed money
movement against a Person's running balance with the LEG.

Sign-convention glossary (read this before touching anything here):

    Internal convention (identical to `BillingRunItem.net_amount_rappen`,
    see `app.models.billing_run`):
        positive = the person owes the LEG more (an incoming payment is
                   therefore recorded NEGATIVE -- it reduces the debt)
        negative = the LEG owes the person more (an executed payout is
                   therefore recorded POSITIVE -- it neutralizes a credit)

    GUI convention (receivables page only, per the way Michael thinks about
    it -- "positive = Guthaben/zu viel bezahlt, negative = Schulden"):
        the displayed "balance" is the NEGATED running balance.

    The negation happens in exactly one place: the GUI layer that renders
    a person's balance. Never negate anything when storing or summing
    `amount_rappen` here -- that is how a sign bug gets introduced.

Deliberately does NOT duplicate `billing_run_items.net_amount_rappen`
("what was invoiced") into a row here -- that table remains the sole
source of truth for invoiced amounts, so there is no shadow copy that
could drift out of sync. This table only records what the *bank*
actually confirmed: payments received, payouts executed, or a manual
correction. A person's running balance is therefore always

    SUM(billing_run_items.net_amount_rappen) + SUM(account_entries.amount_rappen)

computed live (see `get_balance_rappen`/`get_all_saldi`), never stored.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class AccountEntry:
    """One bank-confirmed (or manually corrected) booking on a Person's
    running account.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        person_id: The Person this booking applies to.
        kind: `"zahlungseingang"` (incoming payment), `"auszahlung"` (an
            executed payout) or `"korrektur"` (a manual booking, e.g. a
            cash payment or a write-off -- see the module docstring for
            the sign convention).
        amount_rappen: Signed amount in Rappen, see the module docstring.
        booked_at: ISO-8601 date/timestamp this booking is dated to (the
            bank's booking date for a bank-confirmed entry).
        billing_run_item_id: The invoice/credit this booking is for, if
            known -- purely for traceability in the person's transaction
            history, never used to compute the balance (that is always a
            person-level sum, see the module docstring).
        bank_transaction_id: The imported bank statement entry this
            booking originated from, or `None` for a manual `korrektur`.
        note: Free-text note (e.g. "Barzahlung", a reason for a korrektur).
        created_at: ISO-8601 timestamp this row was created.
    """

    id: Optional[int]
    person_id: int
    kind: str
    amount_rappen: int
    booked_at: str
    billing_run_item_id: Optional[int]
    bank_transaction_id: Optional[int]
    note: str
    created_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "AccountEntry":
        """Build an `AccountEntry` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `account_entries` table.

        Returns:
            The corresponding `AccountEntry` dataclass instance.
        """
        return AccountEntry(
            id=row["id"],
            person_id=row["person_id"],
            kind=row["kind"],
            amount_rappen=row["amount_rappen"],
            booked_at=row["booked_at"],
            billing_run_item_id=row["billing_run_item_id"],
            bank_transaction_id=row["bank_transaction_id"],
            note=row["note"],
            created_at=row["created_at"],
        )


def create(
    connection: sqlite3.Connection,
    *,
    person_id: int,
    kind: str,
    amount_rappen: int,
    booked_at: str,
    billing_run_item_id: Optional[int] = None,
    bank_transaction_id: Optional[int] = None,
    note: str = "",
    commit: bool = True,
) -> int:
    """Insert one account entry.

    Args:
        connection: Open SQLite connection.
        person_id: The Person this booking applies to.
        kind: `"zahlungseingang"`, `"auszahlung"` or `"korrektur"`.
        amount_rappen: Signed amount in Rappen (see module docstring).
        booked_at: ISO-8601 date/timestamp this booking is dated to.
        billing_run_item_id: The invoice/credit this pays, if known.
        bank_transaction_id: The bank statement entry this originated
            from, if any.
        note: Free-text note.
        commit: Whether to commit immediately. Set `False` when the
            caller is booking many entries in one request (e.g. a bank
            statement import covering dozens of transactions, see
            `app.domain.bank_reconciliation.book_transaction`) and relies
            on its own enclosing `app.db.connection.connection_scope` to
            commit once at the end -- avoids one SQLite fsync per row.

    Returns:
        The primary key of the newly created entry.
    """
    cursor = connection.execute(
        """
        INSERT INTO account_entries
            (person_id, kind, amount_rappen, booked_at, billing_run_item_id,
             bank_transaction_id, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            person_id,
            kind,
            amount_rappen,
            booked_at,
            billing_run_item_id,
            bank_transaction_id,
            note,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    if commit:
        connection.commit()
    return cursor.lastrowid


def delete(connection: sqlite3.Connection, entry_id: int) -> None:
    """Remove an account entry (used by `undo_match` to reverse a wrong
    bank-transaction match).

    Args:
        connection: Open SQLite connection.
        entry_id: Primary key of the entry to remove.

    Returns:
        None.
    """
    connection.execute("DELETE FROM account_entries WHERE id = ?", (entry_id,))
    connection.commit()


def list_for_person(connection: sqlite3.Connection, person_id: int) -> list[AccountEntry]:
    """List every account entry for one person, oldest first.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.

    Returns:
        That person's account entries, ordered by `booked_at`.
    """
    rows = connection.execute(
        "SELECT * FROM account_entries WHERE person_id = ? ORDER BY booked_at, id",
        (person_id,),
    ).fetchall()
    return [AccountEntry.from_row(row) for row in rows]


def get_balance_rappen(connection: sqlite3.Connection, person_id: int) -> int:
    """Compute one person's current running balance, internal sign convention.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.

    Returns:
        Positive if the person owes the LEG, negative if the LEG owes the
        person (see module docstring) -- the GUI negates this for display.
    """
    row = connection.execute(
        """
        SELECT
            COALESCE((SELECT SUM(net_amount_rappen) FROM billing_run_items WHERE person_id = ?), 0)
            + COALESCE((SELECT SUM(amount_rappen) FROM account_entries WHERE person_id = ?), 0)
            AS balance
        """,
        (person_id, person_id),
    ).fetchone()
    return row["balance"]


def get_remaining_for_item(connection: sqlite3.Connection, item_id: int, net_amount_rappen: int) -> int:
    """Compute how much of one specific billing run item is still open.

    Unlike the person-level balance (which nets everything together and is
    what gates whether a dunning notice is raised at all, see `app.domain.
    dunning`), this only counts payments/corrections explicitly linked
    to *this* item via `billing_run_item_id` -- the well-defined subset a
    dunning notice's own QR-bill can safely charge without guessing how a
    person-level payment not tied to any specific invoice should be
    allocated across several open items.

    Args:
        connection: Open SQLite connection.
        item_id: Primary key of the billing run item.
        net_amount_rappen: That item's own `net_amount_rappen` (passed in
            rather than re-fetched, since callers already have the item).

    Returns:
        The remaining amount in internal sign convention (positive =
        still owed to the LEG), never negative -- clamped to `0` once
        item-linked payments cover the item, even if they overshoot it
        (the excess becomes a general credit on the person's balance, not a
        negative "remaining" on this one item).
    """
    row = connection.execute(
        "SELECT COALESCE(SUM(amount_rappen), 0) AS total FROM account_entries WHERE billing_run_item_id = ?",
        (item_id,),
    ).fetchone()
    return max(0, net_amount_rappen + row["total"])


def get_all_saldi(connection: sqlite3.Connection) -> dict[int, int]:
    """Compute every person's current running balance in one grouped query.

    Avoids an N+1 query pattern on the receivables list page -- see
    `_load_metering_point_lookup` in `app.importers.import_service` for the
    same rationale applied elsewhere in this codebase.

    Args:
        connection: Open SQLite connection.

    Returns:
        A dict mapping `person_id` to its balance (internal sign
        convention). A person with neither billing history nor account
        entries is simply absent (treat a missing key as `0`).
    """
    rows = connection.execute(
        """
        SELECT person_id, SUM(net_amount_rappen) AS total FROM billing_run_items
        GROUP BY person_id
        """
    ).fetchall()
    saldi: dict[int, int] = {row["person_id"]: row["total"] for row in rows}

    rows = connection.execute(
        """
        SELECT person_id, SUM(amount_rappen) AS total FROM account_entries
        GROUP BY person_id
        """
    ).fetchall()
    for row in rows:
        saldi[row["person_id"]] = saldi.get(row["person_id"], 0) + row["total"]

    return saldi
