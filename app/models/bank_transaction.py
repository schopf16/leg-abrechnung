"""Imported bank statement (camt.053/camt.054) batches and their entries.

Every entry parsed from an imported file is stored here regardless of
whether it could be matched to a Person -- see `app.domain.
bank_reconciliation` for the matching logic itself. Storing every entry,
matched or not, means nothing imported is ever silently lost: an entry
that could not be matched at import time simply sits with
`status = "unmatched"` until someone matches or explicitly ignores it.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

#: Statuses a `BankTransaction` can be in, see `app.domain.
#: bank_reconciliation` for the transitions between them.
OPEN_STATUSES = ("unmatched", "suggested_pending_review")


@dataclass
class BankImportBatch:
    """One completed import of a camt.053/camt.054 file.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        filename: Original filename, for display in the import history.
        imported_at: ISO-8601 timestamp the import completed.
        account_iban: The statement's own account IBAN, if present.
        statement_from: Start of the covered period, if the file states one.
        statement_to: End of the covered period, if the file states one.
        entry_count: Number of `BankTransaction` rows this batch produced
            (including ones skipped as duplicates of an earlier import).
    """

    id: Optional[int]
    filename: str
    imported_at: str
    account_iban: str
    statement_from: Optional[str]
    statement_to: Optional[str]
    entry_count: int

    @staticmethod
    def from_row(row: sqlite3.Row) -> "BankImportBatch":
        """Build a `BankImportBatch` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `bank_import_batches` table.

        Returns:
            The corresponding `BankImportBatch` dataclass instance.
        """
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
    """One entry (`TxDtls`) from an imported bank statement.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        bank_import_batch_id: The import batch this entry came from.
        bank_reference: The bank's own unique reference for this entry
            (`AcctSvcrRef`/`NtryRef`), or a synthesized fallback key if
            neither was present -- see `app.importers.camt_parser`. Used
            (together with `currency`/`amount_rappen`/
            `credit_debit_indicator`) as the idempotency key that prevents
            a re-imported/overlapping statement from duplicating bookings.
        booking_date: ISO date the bank booked this entry.
        amount_rappen: Absolute amount in Rappen (always non-negative);
            direction is `credit_debit_indicator`, not the sign.
        currency: ISO 4217 code -- always `"CHF"` in practice, since the
            parser rejects (with a warning) any other currency.
        credit_debit_indicator: `"CRDT"` (money in) or `"DBIT"` (money out).
        counterparty_name: The other party's name (debtor for a CRDT
            entry, creditor for a DBIT entry).
        counterparty_iban: The other party's IBAN, normalized, or `""`.
        structured_reference: Raw digits from `Strd/CdtrRefInf/Ref`, or
            `""` if the entry carries no structured reference.
        remittance_text: Raw unstructured remittance text (`Ustrd`), or `""`.
        source_format: `"camt053"` or `"camt054"`, whichever file this was
            imported from.
        is_reversal: Whether the bank flagged this entry as a reversal
            (`RvslInd`) -- if so, it is never auto-matched (see
            `app.domain.bank_reconciliation`), regardless of what its
            reference decodes to.
        status: One of `"auto_matched"`, `"suggested_pending_review"`,
            `"manually_matched"`, `"unmatched"`, `"ignored"`.
        matched_person_id: The Person this was matched to, once resolved.
        account_entry_id: The `AccountEntry` this booking produced, once
            resolved (cleared again by `undo_match`).
        created_at: ISO-8601 timestamp this row was created.
    """

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
        """Build a `BankTransaction` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `bank_transactions` table.

        Returns:
            The corresponding `BankTransaction` dataclass instance.
        """
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
    """Record one completed bank statement import.

    Args:
        connection: Open SQLite connection.
        filename: Original filename.
        account_iban: The statement's own account IBAN, if present.
        statement_from: Start of the covered period, if stated.
        statement_to: End of the covered period, if stated.
        entry_count: Number of entries this import produced.

    Returns:
        The primary key of the newly created batch.
    """
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
    """Insert one parsed bank statement entry, skipping it if it is a
    duplicate of an already-imported entry (idempotent re-import).

    Args:
        connection: Open SQLite connection.
        bank_import_batch_id: The import batch this entry belongs to.
        bank_reference: The bank's own reference, or a synthesized
            fallback key -- see `app.importers.camt_parser`.
        booking_date: ISO date the bank booked this entry.
        amount_rappen: Absolute amount in Rappen.
        currency: ISO 4217 code.
        credit_debit_indicator: `"CRDT"` or `"DBIT"`.
        counterparty_name: The other party's name.
        counterparty_iban: The other party's normalized IBAN, or `""`.
        structured_reference: Raw structured reference digits, or `""`.
        remittance_text: Raw unstructured remittance text, or `""`.
        source_format: `"camt053"` or `"camt054"`.
        is_reversal: Whether the bank flagged this as a reversal.
        commit: Whether to commit immediately -- see `app.models.
            account_entry.create`'s `commit` parameter for the rationale;
            an import batch calls this once per parsed transaction and
            relies on the enclosing `connection_scope` to commit once.

    Returns:
        The primary key of the newly inserted row, or `None` if this
        exact entry (same `bank_reference`/`currency`/`amount_rappen`/
        `credit_debit_indicator`) was already imported by an earlier
        batch -- deliberately `INSERT ... ON CONFLICT DO NOTHING`, not
        `DO UPDATE`: an already-reviewed entry's match must never be
        silently reset by a later overlapping import.
    """
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
    """Fetch a single bank transaction by id.

    Args:
        connection: Open SQLite connection.
        transaction_id: Primary key of the transaction.

    Returns:
        The matching `BankTransaction`, or `None` if no such id exists.
    """
    row = connection.execute(
        "SELECT * FROM bank_transactions WHERE id = ?", (transaction_id,)
    ).fetchone()
    return BankTransaction.from_row(row) if row else None


def list_open(connection: sqlite3.Connection) -> list[BankTransaction]:
    """List every bank transaction still needing a human decision.

    Args:
        connection: Open SQLite connection.

    Returns:
        Transactions with `status` in `OPEN_STATUSES`, across every
        import batch, most recent booking date first.
    """
    placeholders = ", ".join("?" for _ in OPEN_STATUSES)
    rows = connection.execute(
        f"""
        SELECT * FROM bank_transactions
        WHERE status IN ({placeholders})
        ORDER BY booking_date DESC, id DESC
        """,
        OPEN_STATUSES,
    ).fetchall()
    return [BankTransaction.from_row(row) for row in rows]


def list_recent(connection: sqlite3.Connection, limit: int = 20) -> list[BankTransaction]:
    """List the most recently created bank transactions, any status.

    Used to offer "Assignment rückgängig machen" on a just-matched entry
    without having to hunt through a whole import batch.

    Args:
        connection: Open SQLite connection.
        limit: Maximum number of rows to return.

    Returns:
        Up to `limit` transactions, most recently created first.
    """
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
    """Record that a bank transaction was matched and booked.

    Args:
        connection: Open SQLite connection.
        transaction_id: Primary key of the transaction.
        status: `"auto_matched"` or `"manually_matched"`.
        matched_person_id: The Person this was matched to.
        account_entry_id: The `AccountEntry` this booking produced.
        commit: Whether to commit immediately -- see `app.models.
            account_entry.create`'s `commit` parameter for the rationale.

    Returns:
        None.
    """
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
    """Set a bank transaction's status without touching its match fields.

    Used for `"suggested_pending_review"`/`"unmatched"` (no match yet) and
    `"ignored"` (a human decided this entry needs no booking).

    Args:
        connection: Open SQLite connection.
        transaction_id: Primary key of the transaction.
        status: The new status.
        commit: Whether to commit immediately -- see `app.models.
            account_entry.create`'s `commit` parameter for the rationale.

    Returns:
        None.
    """
    connection.execute(
        "UPDATE bank_transactions SET status = ? WHERE id = ?",
        (status, transaction_id),
    )
    if commit:
        connection.commit()


def clear_match(connection: sqlite3.Connection, transaction_id: int, *, status: str) -> None:
    """Undo a bank transaction's match, resetting it back to open.

    Args:
        connection: Open SQLite connection.
        transaction_id: Primary key of the transaction.
        status: The status to fall back to (`"unmatched"` or
            `"suggested_pending_review"`, depending on whether candidates
            still exist -- decided by the caller, see `app.domain.
            bank_reconciliation.undo_match`).

    Returns:
        None.
    """
    connection.execute(
        """
        UPDATE bank_transactions
        SET status = ?, matched_person_id = NULL, account_entry_id = NULL
        WHERE id = ?
        """,
        (status, transaction_id),
    )
    connection.commit()
