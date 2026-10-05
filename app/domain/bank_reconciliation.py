"""Matches imported bank statement entries (see `app.importers.camt_parser`) to a Person and, where
applicable, a specific invoice/payout, and books the result as an `AccountEntry`."""

import difflib
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from app.domain.iban_validation import normalize_iban
from app.importers.camt_parser import ParsedBankTransaction
from app.models import account_entry as account_entry_repo
from app.models import bank_transaction as bank_transaction_repo
from app.models import billing_run as billing_run_repo
from app.models import person as person_repo
from app.pdf.qr_reference import parse_qrr_reference

#: Minimum `difflib` similarity ratio for a name-based candidate to be
#: surfaced at all -- below this, the guess is too weak to be useful.
_NAME_SIMILARITY_THRESHOLD = 0.6

#: Maximum number of candidates shown for one transaction.
_MAX_CANDIDATES = 5


@dataclass
class MatchCandidate:
    """One candidate Person (and, if found, a specific invoice/payout) for a bank transaction that..."""

    person_id: int
    person_name: str
    billing_run_item_id: Optional[int]
    confidence: str
    name_similarity: Optional[float] = None


@dataclass
class MatchResult:
    """Outcome of `find_match` for one parsed bank transaction."""

    status: str
    matched_person_id: Optional[int] = None
    matched_billing_run_item_id: Optional[int] = None
    candidates: list[MatchCandidate] = field(default_factory=list)


def find_match(connection: sqlite3.Connection, transaction: ParsedBankTransaction) -> MatchResult:
    """Determine how a parsed bank transaction should be matched."""
    if not transaction.is_reversal and transaction.credit_debit_indicator == "CRDT":
        auto = _try_auto_match(connection, transaction)
        if auto is not None:
            return auto

    candidates = _find_candidates(connection, transaction)
    if candidates:
        return MatchResult(status="suggested_pending_review", candidates=candidates)
    return MatchResult(status="unmatched")


def _try_auto_match(
    connection: sqlite3.Connection, transaction: ParsedBankTransaction
) -> Optional[MatchResult]:
    """Attempt the deterministic QRR-decode auto-match path."""
    if not transaction.structured_reference:
        return None
    decoded = parse_qrr_reference(transaction.structured_reference)
    if decoded is None:
        return None
    item = billing_run_repo.get_item(connection, decoded.item_id)
    if item is None or item.billing_run_id != decoded.billing_run_id:
        return None
    person = person_repo.get(connection, item.person_id)
    if person is None or person.customer_number != decoded.customer_number:
        return None
    return MatchResult(
        status="auto_matched", matched_person_id=person.id, matched_billing_run_item_id=item.id
    )


def _find_candidates(
    connection: sqlite3.Connection, transaction: ParsedBankTransaction
) -> list[MatchCandidate]:
    """Find ranked candidate Persons for a transaction needing review."""
    counterparty_iban = normalize_iban(transaction.counterparty_iban) if transaction.counterparty_iban else ""
    remittance_numbers = set(re.findall(r"\d{6}", transaction.remittance_text))

    best_by_person: dict[int, MatchCandidate] = {}
    for candidate_person in person_repo.list_all(connection):
        confidence = None
        name_similarity = None

        if (
            counterparty_iban
            and candidate_person.iban
            and normalize_iban(candidate_person.iban) == counterparty_iban
        ):
            confidence = "iban_exact"
        elif (
            candidate_person.customer_number is not None
            and str(candidate_person.customer_number) in remittance_numbers
        ):
            confidence = "customer_number_text"
        else:
            ratio = difflib.SequenceMatcher(
                None,
                transaction.counterparty_name.strip().lower(),
                candidate_person.display_name.strip().lower(),
            ).ratio()
            if ratio >= _NAME_SIMILARITY_THRESHOLD:
                confidence = "name_amount"
                name_similarity = ratio

        if confidence is None:
            continue

        # `person_repo.list_all` yields each person exactly once, so
        # `candidate_person.id` is never seen twice here -- a plain
        # assignment, no merge-with-existing check needed.
        best_by_person[candidate_person.id] = MatchCandidate(
            person_id=candidate_person.id,
            person_name=candidate_person.display_name,
            billing_run_item_id=_best_matching_item_id(connection, candidate_person.id, transaction),
            confidence=confidence,
            name_similarity=name_similarity,
        )

    ranked = sorted(best_by_person.values(), key=_priority, reverse=True)
    return ranked[:_MAX_CANDIDATES]


def _priority_for(confidence: str, name_similarity: Optional[float]) -> float:
    """Sort key for a not-yet-built candidate, used to decide whether a newly found signal should..."""
    if confidence == "iban_exact":
        return 3.0
    if confidence == "customer_number_text":
        return 2.0
    return 1.0 + (name_similarity or 0.0)


def _priority(candidate: MatchCandidate) -> float:
    """`_priority_for` applied to an already-built `MatchCandidate`."""
    return _priority_for(candidate.confidence, candidate.name_similarity)


def _best_matching_item_id(
    connection: sqlite3.Connection, person_id: int, transaction: ParsedBankTransaction
) -> Optional[int]:
    """Find an open invoice/payout of this person whose amount matches the transaction, to attach for..."""
    for item in billing_run_repo.list_items_for_person(connection, person_id):
        if transaction.credit_debit_indicator == "CRDT" and item.is_owed_to_leg:
            if item.net_amount_rappen == transaction.amount_rappen:
                return item.id
        elif transaction.credit_debit_indicator == "DBIT" and item.is_owed_by_leg:
            if abs(item.net_amount_rappen) == transaction.amount_rappen:
                return item.id
    return None


def book_transaction(
    connection: sqlite3.Connection,
    *,
    bank_import_batch_id: int,
    transaction: ParsedBankTransaction,
    source_format: str,
    person_id: Optional[int],
    billing_run_item_id: Optional[int],
    status: str,
    commit: bool = True,
) -> Optional[int]:
    """Store one parsed bank transaction and, if resolved, book its `AccountEntry`."""
    transaction_id = bank_transaction_repo.insert_transaction(
        connection,
        bank_import_batch_id=bank_import_batch_id,
        bank_reference=transaction.bank_reference,
        booking_date=transaction.booking_date,
        amount_rappen=transaction.amount_rappen,
        currency=transaction.currency,
        credit_debit_indicator=transaction.credit_debit_indicator,
        counterparty_name=transaction.counterparty_name,
        counterparty_iban=transaction.counterparty_iban,
        structured_reference=transaction.structured_reference,
        remittance_text=transaction.remittance_text,
        source_format=source_format,
        is_reversal=transaction.is_reversal,
        commit=commit,
    )
    if transaction_id is None:
        return None

    if status == "ignored":
        bank_transaction_repo.set_status(connection, transaction_id, "ignored", commit=commit)
        return transaction_id
    if person_id is None:
        bank_transaction_repo.set_status(connection, transaction_id, status, commit=commit)
        return transaction_id

    entry_id = _book_account_entry(
        connection,
        person_id=person_id,
        credit_debit_indicator=transaction.credit_debit_indicator,
        amount_rappen=transaction.amount_rappen,
        booking_date=transaction.booking_date,
        billing_run_item_id=billing_run_item_id,
        bank_transaction_id=transaction_id,
        commit=commit,
    )
    bank_transaction_repo.set_match(
        connection,
        transaction_id,
        status=status,
        matched_person_id=person_id,
        account_entry_id=entry_id,
        commit=commit,
    )
    return transaction_id


def _book_account_entry(
    connection: sqlite3.Connection,
    *,
    person_id: int,
    credit_debit_indicator: str,
    amount_rappen: int,
    booking_date: str,
    billing_run_item_id: Optional[int] = None,
    bank_transaction_id: Optional[int] = None,
    commit: bool = True,
) -> int:
    """Create the `AccountEntry` for one resolved bank transaction."""
    kind = "payment_received" if credit_debit_indicator == "CRDT" else "payout"
    # Internal sign convention (see app.models.account_entry): an
    # incoming payment reduces what the person owes (negative), an
    # executed payout neutralizes a credit (positive).
    signed_amount_rappen = -amount_rappen if kind == "payment_received" else amount_rappen
    return account_entry_repo.create(
        connection,
        person_id=person_id,
        kind=kind,
        amount_rappen=signed_amount_rappen,
        booked_at=booking_date,
        billing_run_item_id=billing_run_item_id,
        bank_transaction_id=bank_transaction_id,
        commit=commit,
    )


def resolve_open_transaction(
    connection: sqlite3.Connection, bank_transaction_id: int, *, person_id: Optional[int]
) -> None:
    """Resolve one already-imported, still-open bank transaction."""
    transaction = bank_transaction_repo.get(connection, bank_transaction_id)
    if transaction is None:
        raise ValueError(f"Keine Bank-Buchung mit id={bank_transaction_id}.")

    if person_id is None:
        bank_transaction_repo.set_status(connection, bank_transaction_id, "ignored")
        return

    entry_id = _book_account_entry(
        connection,
        person_id=person_id,
        credit_debit_indicator=transaction.credit_debit_indicator,
        amount_rappen=transaction.amount_rappen,
        booking_date=transaction.booking_date,
        bank_transaction_id=bank_transaction_id,
    )
    bank_transaction_repo.set_match(
        connection,
        bank_transaction_id,
        status="manually_matched",
        matched_person_id=person_id,
        account_entry_id=entry_id,
    )


def undo_match(connection: sqlite3.Connection, bank_transaction_id: int) -> None:
    """Reverse a bank transaction's match: delete its `AccountEntry` and reset it back to an open..."""
    transaction = bank_transaction_repo.get(connection, bank_transaction_id)
    if transaction is None:
        raise ValueError(f"Keine Bank-Buchung mit id={bank_transaction_id}.")

    if transaction.account_entry_id is not None:
        account_entry_repo.delete(connection, transaction.account_entry_id)

    parsed = ParsedBankTransaction(
        bank_reference=transaction.bank_reference,
        booking_date=transaction.booking_date,
        amount_rappen=transaction.amount_rappen,
        currency=transaction.currency,
        credit_debit_indicator=transaction.credit_debit_indicator,
        counterparty_name=transaction.counterparty_name,
        counterparty_iban=transaction.counterparty_iban,
        structured_reference=transaction.structured_reference,
        remittance_text=transaction.remittance_text,
        is_reversal=transaction.is_reversal,
    )
    fallback_status = "suggested_pending_review" if _find_candidates(connection, parsed) else "unmatched"
    bank_transaction_repo.clear_match(connection, bank_transaction_id, status=fallback_status)
