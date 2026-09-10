"""Tests for app.domain.bank_reconciliation (QRR auto-match, fuzzy
candidate suggestions, booking, and undo)."""

from app.domain import bank_reconciliation
from app.importers.camt_parser import ParsedBankTransaction
from app.models import account_entry as account_entry_repo
from app.models import bank_transaction as bank_transaction_repo
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.person import Person
from app.pdf.qr_reference import generate_qrr_reference


def _person(db, *, name="Anna", email="anna@example.invalid", iban="", customer_number_hint=None) -> "Person":
    person_id = person_repo.create(
        db,
        Person(
            id=None, salutation="Frau", company="", first_name=name, last_name="Muster",
            contact_email=email, contact_phone="",
            billing_street="", billing_house_number="", billing_postal_code="",
            billing_city="", billing_country="CH",
            iban=iban, customer_number=None, bkw_customer_number=None,
            paper_invoice=False, active=True, created_at="",
        ),
    )
    return person_repo.get(db, person_id)


def _billing_item(db, person_id: int, net_amount_rappen: int) -> tuple[int, int]:
    leg_id = leg_repo.create(
        db, Leg(id=None, name=f"LEG {person_id}-{net_amount_rappen}-{id(object())}", note="", created_at="")
    )
    run_id = billing_run_repo.create_run(
        db,
        BillingRun(
            id=None, leg_id=leg_id, period_year=2026, period_quarter=1,
            created_at="", price_rp_per_kwh=20.0, status="erstellt", notes="",
        ),
    )
    item_ids = billing_run_repo.add_items(
        db,
        [
            BillingRunItem(
                id=None, billing_run_id=run_id, person_id=person_id,
                consumed_kwh=10.0, produced_kwh=0.0, price_rp_per_kwh=20.0,
                admin_fee_consumption_rappen=0, paper_invoice_rappen=0,
                net_amount_rappen=net_amount_rappen, pdf_path=None, created_at="",
            )
        ],
    )
    return run_id, item_ids[0]


def _tx(**overrides) -> ParsedBankTransaction:
    defaults = dict(
        bank_reference="REF-1", booking_date="2026-02-01", amount_rappen=10_000,
        currency="CHF", credit_debit_indicator="CRDT", counterparty_name="Muster Anna",
        counterparty_iban="", structured_reference="", remittance_text="", is_reversal=False,
    )
    defaults.update(overrides)
    return ParsedBankTransaction(**defaults)


def test_valid_qrr_reference_auto_matches(db):
    person = _person(db)
    run_id, item_id = _billing_item(db, person.id, net_amount_rappen=10_000)
    reference = generate_qrr_reference(person.customer_number, run_id, item_id)
    tx = _tx(amount_rappen=10_000, structured_reference=reference)

    result = bank_reconciliation.find_match(db, tx)

    assert result.status == "auto_matched"
    assert result.matched_person_id == person.id
    assert result.matched_billing_run_item_id == item_id


def test_qrr_reference_with_mismatched_customer_number_falls_through_to_candidates(db):
    """A structurally valid reference whose decoded customer_number does not
    match the item's actual person must not be trusted -- it should fall
    back to the suggestion path instead of a wrong auto-match."""
    person = _person(db, iban="CH9300762011623852957")
    run_id, item_id = _billing_item(db, person.id, net_amount_rappen=10_000)
    # A reference encoding a *different* customer_number than the real one.
    corrupted_reference = generate_qrr_reference(999999, run_id, item_id)
    tx = _tx(
        amount_rappen=10_000, structured_reference=corrupted_reference,
        counterparty_iban="CH9300762011623852957",
    )

    result = bank_reconciliation.find_match(db, tx)

    assert result.status == "suggested_pending_review"
    assert any(c.person_id == person.id for c in result.candidates)


def test_reversal_is_never_auto_matched_even_with_valid_reference(db):
    person = _person(db)
    run_id, item_id = _billing_item(db, person.id, net_amount_rappen=10_000)
    reference = generate_qrr_reference(person.customer_number, run_id, item_id)
    tx = _tx(amount_rappen=10_000, structured_reference=reference, is_reversal=True)

    result = bank_reconciliation.find_match(db, tx)

    assert result.status != "auto_matched"


def test_iban_exact_match_ranks_above_name_similarity(db):
    exact = _person(db, name="Anna", iban="CH9300762011623852957")
    similar_name = _person(db, name="Ana", email="ana@example.invalid")
    tx = _tx(counterparty_name="Ana", counterparty_iban="CH9300762011623852957")

    result = bank_reconciliation.find_match(db, tx)

    assert result.status == "suggested_pending_review"
    assert result.candidates[0].person_id == exact.id
    assert result.candidates[0].confidence == "iban_exact"


def test_customer_number_in_remittance_text_is_a_strong_candidate(db):
    person = _person(db, name="Bettina")
    tx = _tx(
        counterparty_name="Ganz anderer Name",
        remittance_text=f"Kundennummer {person.customer_number}, danke",
    )

    result = bank_reconciliation.find_match(db, tx)

    assert result.status == "suggested_pending_review"
    assert result.candidates[0].person_id == person.id
    assert result.candidates[0].confidence == "customer_number_text"


def test_ambiguous_name_candidates_are_all_surfaced_without_auto_matching(db):
    _person(db, name="Maxine", email="m1@example.invalid")
    _person(db, name="Maxime", email="m2@example.invalid")
    tx = _tx(counterparty_name="Maxine Muster")

    result = bank_reconciliation.find_match(db, tx)

    assert result.status == "suggested_pending_review"
    assert len(result.candidates) >= 2


def test_no_candidates_at_all_results_in_unmatched(db):
    tx = _tx(counterparty_name="Komplett Unbekannt", remittance_text="nichts hilfreiches")

    result = bank_reconciliation.find_match(db, tx)

    assert result.status == "unmatched"
    assert result.candidates == []


def test_prepayment_before_any_invoice_exists_still_finds_a_candidate(db):
    """A person with zero billing history yet (a prepayment) must still
    be findable by name/IBAN -- amount matching is a bonus, not a filter."""
    person = _person(db, name="Vorauszahler")
    tx = _tx(counterparty_name="Vorauszahler", amount_rappen=99_999)

    result = bank_reconciliation.find_match(db, tx)

    assert result.status == "suggested_pending_review"
    assert result.candidates[0].person_id == person.id
    assert result.candidates[0].billing_run_item_id is None


def test_book_transaction_records_actual_paid_amount_on_overpayment(db):
    """Paying more than invoiced must book the real amount -- the excess
    simply becomes a credit on the running balance, no special-casing."""
    person = _person(db)
    _run_id, item_id = _billing_item(db, person.id, net_amount_rappen=10_000)
    tx = _tx(amount_rappen=15_000)
    batch_id = bank_transaction_repo.create_batch(
        db, filename="x.xml", account_iban="", statement_from=None, statement_to=None, entry_count=1
    )

    bank_reconciliation.book_transaction(
        db, bank_import_batch_id=batch_id, transaction=tx, source_format="camt053",
        person_id=person.id, billing_run_item_id=item_id, status="manually_matched",
    )

    assert account_entry_repo.get_balance_rappen(db, person.id) == -5_000


def test_book_transaction_for_dbit_payout_uses_positive_amount(db):
    person = _person(db)
    _run_id, _item_id = _billing_item(db, person.id, net_amount_rappen=-8_000)
    tx = _tx(credit_debit_indicator="DBIT", amount_rappen=8_000)
    batch_id = bank_transaction_repo.create_batch(
        db, filename="x.xml", account_iban="", statement_from=None, statement_to=None, entry_count=1
    )

    bank_reconciliation.book_transaction(
        db, bank_import_batch_id=batch_id, transaction=tx, source_format="camt053",
        person_id=person.id, billing_run_item_id=None, status="manually_matched",
    )

    assert account_entry_repo.get_balance_rappen(db, person.id) == 0


def test_book_transaction_is_idempotent_across_reimports(db):
    person = _person(db)
    tx = _tx(bank_reference="STABLE-REF")
    batch_1 = bank_transaction_repo.create_batch(
        db, filename="first.xml", account_iban="", statement_from=None, statement_to=None, entry_count=1
    )
    first_id = bank_reconciliation.book_transaction(
        db, bank_import_batch_id=batch_1, transaction=tx, source_format="camt053",
        person_id=person.id, billing_run_item_id=None, status="manually_matched",
    )

    batch_2 = bank_transaction_repo.create_batch(
        db, filename="second.xml", account_iban="", statement_from=None, statement_to=None, entry_count=1
    )
    second_id = bank_reconciliation.book_transaction(
        db, bank_import_batch_id=batch_2, transaction=tx, source_format="camt053",
        person_id=person.id, billing_run_item_id=None, status="manually_matched",
    )

    assert first_id is not None
    assert second_id is None
    assert len(account_entry_repo.list_for_person(db, person.id)) == 1


def test_ignored_transaction_books_nothing(db):
    tx = _tx()
    batch_id = bank_transaction_repo.create_batch(
        db, filename="x.xml", account_iban="", statement_from=None, statement_to=None, entry_count=1
    )

    transaction_id = bank_reconciliation.book_transaction(
        db, bank_import_batch_id=batch_id, transaction=tx, source_format="camt053",
        person_id=None, billing_run_item_id=None, status="ignored",
    )

    stored = bank_transaction_repo.get(db, transaction_id)
    assert stored.status == "ignored"
    assert stored.account_entry_id is None


def test_undo_match_removes_account_entry_and_resets_status(db):
    person = _person(db, iban="CH9300762011623852957")
    tx = _tx(counterparty_iban="CH9300762011623852957")
    batch_id = bank_transaction_repo.create_batch(
        db, filename="x.xml", account_iban="", statement_from=None, statement_to=None, entry_count=1
    )
    transaction_id = bank_reconciliation.book_transaction(
        db, bank_import_batch_id=batch_id, transaction=tx, source_format="camt053",
        person_id=person.id, billing_run_item_id=None, status="manually_matched",
    )
    assert account_entry_repo.get_balance_rappen(db, person.id) != 0

    bank_reconciliation.undo_match(db, transaction_id)

    stored = bank_transaction_repo.get(db, transaction_id)
    assert stored.account_entry_id is None
    assert stored.matched_person_id is None
    # The IBAN candidate is still findable, so it falls back to a
    # suggestion rather than plain "unmatched".
    assert stored.status == "suggested_pending_review"
    assert account_entry_repo.get_balance_rappen(db, person.id) == 0


def test_double_payment_of_the_same_invoice_via_two_bank_references(db):
    """Two distinct real bank transactions (different bank_reference)
    paying the same invoice must both book -- see the receivables plan's
    explicit "no one-payment-per-invoice lock" requirement."""
    person = _person(db)
    _run_id, item_id = _billing_item(db, person.id, net_amount_rappen=10_000)
    batch_id = bank_transaction_repo.create_batch(
        db, filename="x.xml", account_iban="", statement_from=None, statement_to=None, entry_count=2
    )

    bank_reconciliation.book_transaction(
        db, bank_import_batch_id=batch_id, transaction=_tx(bank_reference="REF-A", amount_rappen=10_000),
        source_format="camt053", person_id=person.id, billing_run_item_id=item_id, status="manually_matched",
    )
    bank_reconciliation.book_transaction(
        db, bank_import_batch_id=batch_id, transaction=_tx(bank_reference="REF-B", amount_rappen=10_000),
        source_format="camt053", person_id=person.id, billing_run_item_id=item_id, status="manually_matched",
    )

    assert len(account_entry_repo.list_for_person(db, person.id)) == 2
    assert account_entry_repo.get_balance_rappen(db, person.id) == -10_000


def test_resolve_open_transaction_assigns_a_person_with_the_correct_sign(db):
    """The manual-resolution path used by the permanent "Offene Bank-
    Buchungen" queue on /receivables (Finding #8: previously duplicated the
    booking logic inline instead of sharing it with book_transaction)."""
    person = _person(db)
    batch_id = bank_transaction_repo.create_batch(
        db, filename="x.xml", account_iban="", statement_from=None, statement_to=None, entry_count=1
    )
    transaction_id = bank_reconciliation.book_transaction(
        db, bank_import_batch_id=batch_id, transaction=_tx(), source_format="camt053",
        person_id=None, billing_run_item_id=None, status="unmatched",
    )

    bank_reconciliation.resolve_open_transaction(db, transaction_id, person_id=person.id)

    stored = bank_transaction_repo.get(db, transaction_id)
    assert stored.status == "manually_matched"
    assert stored.matched_person_id == person.id
    assert stored.account_entry_id is not None
    # CRDT booked as a payment reduces the owed amount (internal sign
    # convention, see app.models.account_entry).
    assert account_entry_repo.get_balance_rappen(db, person.id) == -10_000


def test_resolve_open_transaction_with_no_person_ignores_it(db):
    batch_id = bank_transaction_repo.create_batch(
        db, filename="x.xml", account_iban="", statement_from=None, statement_to=None, entry_count=1
    )
    transaction_id = bank_reconciliation.book_transaction(
        db, bank_import_batch_id=batch_id, transaction=_tx(), source_format="camt053",
        person_id=None, billing_run_item_id=None, status="unmatched",
    )

    bank_reconciliation.resolve_open_transaction(db, transaction_id, person_id=None)

    stored = bank_transaction_repo.get(db, transaction_id)
    assert stored.status == "ignored"
    assert stored.account_entry_id is None


def test_book_transaction_with_commit_false_still_books_within_the_connection(db):
    """Finding #12: a bulk statement import passes commit=False for every
    row and relies on the enclosing connection_scope's single commit --
    the row must still be immediately visible on the same connection."""
    person = _person(db)
    batch_id = bank_transaction_repo.create_batch(
        db, filename="x.xml", account_iban="", statement_from=None, statement_to=None, entry_count=1
    )

    transaction_id = bank_reconciliation.book_transaction(
        db, bank_import_batch_id=batch_id, transaction=_tx(), source_format="camt053",
        person_id=person.id, billing_run_item_id=None, status="manually_matched", commit=False,
    )

    assert transaction_id is not None
    assert account_entry_repo.get_balance_rappen(db, person.id) == -10_000
    db.commit()
