"""Per-person account ledger (Debitorenkonto): every bank-confirmed money movement against a Person's
running balance with the LEG."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class AccountEntry:
    """One bank-confirmed (or manually corrected) booking on a Person's running account."""

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
        """Build an `AccountEntry` from a `sqlite3.Row`."""
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
    """Insert one account entry."""
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
    """Remove an account entry (used by `undo_match` to reverse a wrong bank-transaction match)."""
    connection.execute("DELETE FROM account_entries WHERE id = ?", (entry_id,))
    connection.commit()


def list_for_person(connection: sqlite3.Connection, person_id: int) -> list[AccountEntry]:
    """List every account entry for one person, oldest first."""
    rows = connection.execute(
        "SELECT * FROM account_entries WHERE person_id = ? ORDER BY booked_at, id",
        (person_id,),
    ).fetchall()
    return [AccountEntry.from_row(row) for row in rows]


def get_balance_rappen(connection: sqlite3.Connection, person_id: int) -> int:
    """Compute one person's current running balance, internal sign convention."""
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
    """Compute how much of one specific billing run item is still open."""
    row = connection.execute(
        "SELECT COALESCE(SUM(amount_rappen), 0) AS total FROM account_entries WHERE billing_run_item_id = ?",
        (item_id,),
    ).fetchone()
    return max(0, net_amount_rappen + row["total"])


def get_all_saldi(connection: sqlite3.Connection) -> dict[int, int]:
    """Compute every person's current running balance in one grouped query."""
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
