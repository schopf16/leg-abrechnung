"""Imported bank statement (camt.053/camt.054) batches and their entries."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

#: Statuses a `BankTransaction` can be in, see `app.domain.
#: bank_reconciliation` for the transitions between them.
OPEN_STATUSES = ("unmatched", "suggested_pending_review")


@dataclass
class BankImportBatch:
    """One completed import of a camt.053/camt.054 file."""

    id: Optional[int]
    filename: str
    imported_at: str
    account_iban: str
    statement_from: Optional[str]
    statement_to: Optional[str]
    entry_count: int

    @staticmethod
    def from_row(row: sqlite3.Row) -> "BankImportBatch":
        """Build a `BankImportBatch` from a `sqlite3.Row`."""
        return BankImportBatch(
            id=row["id"],
            filename=row["filename"],
            imported_at=row["imported_at"],
            account_iban=row["account_iban"],
            statement_from=row["statement_from"],
            statement_to=row["statement_to"],
            entry_count=row["entry_count"],
        )


@dataclass
class BankTransaction:
    """One entry (`TxDtls`) from an imported bank statement."""

    id: Optional[int]
    bank_import_batch_id: int
    bank_reference: str
    booking_date: str
    amount_rappen: int
    currency: str
    credit_debit_indicator: str
    counterparty_name: str
    counterparty_iban: str
    structured_reference: str
    remittance_text: str
    source_format: str
    is_reversal: bool
    status: str
    matched_person_id: Optional[int]
    account_entry_id: Optional[int]
    created_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "BankTransaction":
        """Build a `BankTransaction` from a `sqlite3.Row`."""
        return BankTransaction(
            id=row["id"],
            bank_import_batch_id=row["bank_import_batch_id"],
            bank_reference=row["bank_reference"],
            booking_date=row["booking_date"],
            amount_rappen=row["amount_rappen"],
            currency=row["currency"],
            credit_debit_indicator=row["credit_debit_indicator"],
            counterparty_name=row["counterparty_name"],
            counterparty_iban=row["counterparty_iban"],
            structured_reference=row["structured_reference"],
            remittance_text=row["remittance_text"],
            source_format=row["source_format"],
            is_reversal=bool(row["is_reversal"]),
            status=row["status"],
            matched_person_id=row["matched_person_id"],
            account_entry_id=row["account_entry_id"],
            created_at=row["created_at"],
        )


def create_batch(
    connection: sqlite3.Connection,
    *,
    filename: str,
    account_iban: str,
    statement_from: Optional[str],
    statement_to: Optional[str],
    entry_count: int,
) -> int:
    """Record one completed bank statement import."""
    cursor = connection.execute(
        """
        INSERT INTO bank_import_batches
            (filename, imported_at, account_iban, statement_from, statement_to, entry_count)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            filename,
            datetime.now(timezone.utc).isoformat(),
            account_iban,
            statement_from,
            statement_to,
            entry_count,
        ),
    )
    connection.commit()
    return cursor.lastrowid


def insert_transaction(
    connection: sqlite3.Connection,
    *,
    bank_import_batch_id: int,
    bank_reference: str,
    booking_date: str,
    amount_rappen: int,
    currency: str,
    credit_debit_indicator: str,
    counterparty_name: str,
    counterparty_iban: str,
    structured_reference: str,
    remittance_text: str,
    source_format: str,
    is_reversal: bool,
    commit: bool = True,
) -> Optional[int]:
    """Insert one parsed bank statement entry, skipping it if it is a duplicate of an already-imported..."""
    cursor = connection.execute(
        """
        INSERT INTO bank_transactions
            (bank_import_batch_id, bank_reference, booking_date, amount_rappen,
             currency, credit_debit_indicator, counterparty_name, counterparty_iban,
             structured_reference, remittance_text, source_format, is_reversal,
             created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (bank_reference, currency, amount_rappen, credit_debit_indicator)
        DO NOTHING
        """,
        (
            bank_import_batch_id,
            bank_reference,
            booking_date,
            amount_rappen,
            currency,
            credit_debit_indicator,
            counterparty_name,
            counterparty_iban,
            structured_reference,
            remittance_text,
            source_format,
            1 if is_reversal else 0,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    if commit:
        connection.commit()
    return cursor.lastrowid if cursor.rowcount > 0 else None


def get(connection: sqlite3.Connection, transaction_id: int) -> Optional[BankTransaction]:
    """Fetch a single bank transaction by id."""
    row = connection.execute("SELECT * FROM bank_transactions WHERE id = ?", (transaction_id,)).fetchone()
    return BankTransaction.from_row(row) if row else None


def list_open(connection: sqlite3.Connection) -> list[BankTransaction]:
    """List every bank transaction still needing a human decision."""
    placeholders = ", ".join("?" for _ in OPEN_STATUSES)
    # Only "?" placeholders are interpolated; the values themselves are bound.
    query = (
        f"SELECT * FROM bank_transactions WHERE status IN ({placeholders}) "  # nosec B608
        "ORDER BY booking_date DESC, id DESC"
    )
    rows = connection.execute(query, OPEN_STATUSES).fetchall()
    return [BankTransaction.from_row(row) for row in rows]


def list_recent(connection: sqlite3.Connection, limit: int = 20) -> list[BankTransaction]:
    """List the most recently created bank transactions, any status."""
    rows = connection.execute(
        "SELECT * FROM bank_transactions ORDER BY created_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [BankTransaction.from_row(row) for row in rows]


def set_match(
    connection: sqlite3.Connection,
    transaction_id: int,
    *,
    status: str,
    matched_person_id: int,
    account_entry_id: int,
    commit: bool = True,
) -> None:
    """Record that a bank transaction was matched and booked."""
    connection.execute(
        """
        UPDATE bank_transactions
        SET status = ?, matched_person_id = ?, account_entry_id = ?
        WHERE id = ?
        """,
        (status, matched_person_id, account_entry_id, transaction_id),
    )
    if commit:
        connection.commit()


def set_status(
    connection: sqlite3.Connection, transaction_id: int, status: str, *, commit: bool = True
) -> None:
    """Set a bank transaction's status without touching its match fields."""
    connection.execute(
        "UPDATE bank_transactions SET status = ? WHERE id = ?",
        (status, transaction_id),
    )
    if commit:
        connection.commit()


def clear_match(connection: sqlite3.Connection, transaction_id: int, *, status: str) -> None:
    """Undo a bank transaction's match, resetting it back to open."""
    connection.execute(
        """
        UPDATE bank_transactions
        SET status = ?, matched_person_id = NULL, account_entry_id = NULL
        WHERE id = ?
        """,
        (status, transaction_id),
    )
    connection.commit()
