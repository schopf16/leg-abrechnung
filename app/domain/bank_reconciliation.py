"""Matches imported bank statement entries (see `app.importers.camt_parser`)
to a Person and, where applicable, a specific invoice/payout, and books
the result as an `AccountEntry`.

Two paths, by design (see the receivables plan):

1. **Deterministic**: a CRDT entry whose structured reference decodes
   (via `app.pdf.qr_reference.parse_qrr_reference`) to a real billing run
   item, whose person's customer number matches the decoded one. This is
   booked automatically -- no human confirmation needed, since the
   reference already proves which invoice this is.

2. **Suggested**: everything else (no/invalid reference, a reversal, or a
   DBIT payout, which never carries a QRR reference at all). Candidates
   are found by exact IBAN match, a customer number mentioned in the
   remittance free text, or name similarity -- never auto-booked, always
   presented for a human to confirm or correct.

A reversal (`is_reversal`) is *never* auto-matched, regardless of what
its reference decodes to -- a reversed payment must not be silently
booked like a normal one.
"""

import difflib
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    """One candidate Person (and, if found, a specific invoice/payout) for
    a bank transaction that could not be auto-matched.

    Attributes:
        person_id: Primary key of the candidate Person.
        person_name: Display name, for the review UI.
        billing_run_item_id: A specific open invoice/payout of this
            person whose amount matches the transaction, if one was
            found -- `None` if no amount match exists (e.g. a
            prepayment before any invoice exists yet for this person).
        confidence: `"iban_exact"`, `"customer_number_text"` or `"name_amount"`.
        name_similarity: The `difflib` ratio behind a `"name_amount"`
            candidate, `None` for the other confidence levels.
    """

    person_id: int
    person_name: str
    billing_run_item_id: Optional[int]
    confidence: str
    name_similarity: Optional[float] = None


@dataclass
class MatchResult:
    """Outcome of `find_match` for one parsed bank transaction.

    Attributes:
        status: `"auto_matched"`, `"suggested_pending_review"` or
            `"unmatched"`.
        matched_person_id: Set only when `status == "auto_matched"`.
        matched_billing_run_item_id: Set only when `status == "auto_matched"`.
        candidates: Ranked candidates, non-empty only when
            `status == "suggested_pending_review"`.
    """

    status: str
    matched_person_id: Optional[int] = None
    matched_billing_run_item_id: Optional[int] = None
    candidates: list[MatchCandidate] = field(default_factory=list)


def find_match(connection: sqlite3.Connection, transaction: ParsedBankTransaction) -> MatchResult:
    """Determine how a parsed bank transaction should be matched.

    Read-only -- books nothing. Used both for the import preview and by
    `book_transaction` immediately before committing.

    Args:
        connection: Open SQLite connection.
        transaction: One parsed bank statement entry.

    Returns:
        The determined `MatchResult`.
    """
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
    """Attempt the deterministic QRR-decode auto-match path.

    Args:
        connection: Open SQLite connection.
        transaction: One parsed bank statement entry (already known to be
            a non-reversal CRDT entry).

    Returns:
        An `"auto_matched"` `MatchResult` if the reference decodes to a
        real item whose person's customer number matches, else `None`
        (falls through to the suggestion path).
    """
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
    """Find ranked candidate Persons for a transaction needing review.

    Searches every Person (active and inactive -- a payment can still
    legitimately arrive against a former member's old debt, see the
    Austritts-/Ausschlussprozess: exclusion ends membership, never the
    claim), not only those with a currently open invoice/payout of a
    matching amount -- otherwise a prepayment made before any invoice
    exists yet for that person would never surface a candidate at all.

    Args:
        connection: Open SQLite connection.
        transaction: One parsed bank statement entry.

    Returns:
        Up to `_MAX_CANDIDATES` candidates, best match first.
    """
    counterparty_iban = normalize_iban(transaction.counterparty_iban) if transaction.counterparty_iban else ""
    remittance_numbers = set(re.findall(r"\d{6}", transaction.remittance_text))

    best_by_person: dict[int, MatchCandidate] = {}
    for candidate_person in person_repo.list_all(connection):
        confidence = None
        name_similarity = None

        if counterparty_iban and candidate_person.iban and normalize_iban(candidate_person.iban) == counterparty_iban:
            confidence = "iban_exact"
        elif candidate_person.customer_number is not None and str(candidate_person.customer_number) in remittance_numbers:
            confidence = "customer_number_text"
        else:
            ratio = difflib.SequenceMatcher(
                None, transaction.counterparty_name.strip().lower(), candidate_person.display_name.strip().lower()
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
    """Sort key for a not-yet-built candidate, used to decide whether a
    newly found signal should replace an already-recorded one for the
    same person.

    Args:
        confidence: The candidate's confidence level.
        name_similarity: Its `difflib` ratio, if `confidence` is
            `"name_amount"`.

    Returns:
        A comparable priority value, higher is better.
    """
    if confidence == "iban_exact":
        return 3.0
    if confidence == "customer_number_text":
        return 2.0
    return 1.0 + (name_similarity or 0.0)


def _priority(candidate: MatchCandidate) -> float:
    """`_priority_for` applied to an already-built `MatchCandidate`.

    Args:
        candidate: The candidate to score.

    Returns:
        Its priority value, higher is better.
    """
    return _priority_for(candidate.confidence, candidate.name_similarity)


def _best_matching_item_id(
    connection: sqlite3.Connection, person_id: int, transaction: ParsedBankTransaction
) -> Optional[int]:
    """Find an open invoice/payout of this person whose amount matches
    the transaction, to attach for traceability (never a hard filter).

    Args:
        connection: Open SQLite connection.
        person_id: Candidate person.
        transaction: One parsed bank statement entry.

    Returns:
        A matching `BillingRunItem`'s id, or `None` if none of that
        person's items has a matching amount (e.g. a prepayment).
    """
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
    """Store one parsed bank transaction and, if resolved, book its
    `AccountEntry`.

    Args:
        connection: Open SQLite connection.
        bank_import_batch_id: The import batch this entry belongs to.
        transaction: The parsed entry.
        source_format: `"camt053"` or `"camt054"`, whichever file this
            entry was parsed from.
        person_id: The Person to book against, or `None` to store the
            transaction without booking anything yet (`status` must then
            be `"unmatched"` or `"suggested_pending_review"`).
        billing_run_item_id: The specific invoice/payout this pays, if any.
        status: The `bank_transactions.status` to record -- `"ignored"`
            also stores with no booking.
        commit: Whether each underlying write commits immediately. A
            statement import calls this once per parsed transaction
            (potentially dozens) within one `connection_scope` -- pass
            `False` there so only that enclosing scope commits, instead of
            one SQLite fsync per row (see `app.models.account_entry.
            create`'s `commit` parameter).

    Returns:
        The new `bank_transactions.id`, or `None` if this exact entry was
        already imported by an earlier batch (see `app.models.
        bank_transaction.insert_transaction`'s idempotency contract) --
        in that case nothing else happens: no duplicate booking, no
        duplicate row, the earlier import's resolution is left untouched.
    """
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
        connection, transaction_id, status=status, matched_person_id=person_id, account_entry_id=entry_id,
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
    """Create the `AccountEntry` for one resolved bank transaction.

    The single place the CRDT/DBIT-to-`kind`/sign mapping is written --
    shared by `book_transaction` (fresh import) and `resolve_open_transaction`
    (resolving an already-stored, still-open transaction from the
    permanent "Offene Bank-Buchungen" queue on `/receivables`) so the two
    call sites can never drift apart on the sign convention.

    Args:
        connection: Open SQLite connection.
        person_id: The Person to book against.
        credit_debit_indicator: `"CRDT"` or `"DBIT"`.
        amount_rappen: Always-positive transaction amount.
        booking_date: ISO-8601 date this booking is dated to.
        billing_run_item_id: The specific invoice/payout this pays, if any.
        bank_transaction_id: The bank transaction this originated from.
        commit: Whether to commit immediately -- see `app.models.
            account_entry.create`'s `commit` parameter for the rationale.

    Returns:
        The new `account_entries.id`.
    """
    kind = "zahlungseingang" if credit_debit_indicator == "CRDT" else "auszahlung"
    # Internal sign convention (see app.models.account_entry): an
    # incoming payment reduces what the person owes (negative), an
    # executed payout neutralizes a credit (positive).
    signed_amount_rappen = -amount_rappen if kind == "zahlungseingang" else amount_rappen
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
    """Resolve one already-imported, still-open bank transaction.

    The manual-resolution counterpart to `book_transaction`: used by the
    permanent "Offene Bank-Buchungen" queue on `/receivables` to assign (or
    ignore) a transaction left unresolved from an earlier import, whereas
    `book_transaction` only ever runs once, while committing a fresh
    import batch.

    Args:
        connection: Open SQLite connection.
        bank_transaction_id: Primary key of the already-stored transaction.
        person_id: The Person to book against, or `None` to mark the
            transaction `"ignored"` instead.

    Returns:
        None.

    Raises:
        ValueError: If no such transaction exists.
    """
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
        connection, bank_transaction_id, status="manually_matched",
        matched_person_id=person_id, account_entry_id=entry_id,
    )


def undo_match(connection: sqlite3.Connection, bank_transaction_id: int) -> None:
    """Reverse a bank transaction's match: delete its `AccountEntry` and
    reset it back to an open status.

    Re-runs candidate search on the stored entry so it falls back to
    `"suggested_pending_review"` (not plain `"unmatched"`) if candidates
    still exist -- e.g. undoing a wrong manual match should not hide the
    right suggestion that was available all along.

    Args:
        connection: Open SQLite connection.
        bank_transaction_id: Primary key of the transaction to unmatch.

    Returns:
        None.

    Raises:
        ValueError: If no such transaction exists.
    """
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
