"""Tests for app.models.bank_transaction (imported camt.053/camt.054
entries and their import batches)."""

from app.models import account_entry as account_entry_repo
from app.models import bank_transaction as bank_transaction_repo
from app.models import person as person_repo
from app.models.person import Person


def _person(db, email: str = "p@example.invalid") -> int:
    return person_repo.create(
        db,
        Person(
            id=None, anrede="Frau", firma="", vorname="P", nachname="Test",
            kontakt_email=email, kontakt_telefon="",
            rechnungsadresse_strasse="", rechnungsadresse_hausnummer="", rechnungsadresse_plz="",
            rechnungsadresse_ort="", rechnungsadresse_land="CH",
            iban="", kundennummer=None, bkw_kundennummer=None,
            papierrechnung=False, aktiv=True, created_at="",
        ),
    )


def _batch(db, filename: str = "auszug.xml") -> int:
    return bank_transaction_repo.create_batch(
        db, filename=filename, account_iban="CH9300762011623852957",
        statement_from="2026-01-01", statement_to="2026-01-31", entry_count=1,
    )


def _insert(db, batch_id: int, **overrides) -> int:
    defaults = dict(
        bank_import_batch_id=batch_id,
        bank_reference="REF-001",
        booking_date="2026-01-15",
        amount_rappen=10_000,
        currency="CHF",
        credit_debit_indicator="CRDT",
        counterparty_name="Muster Maxine",
        counterparty_iban="",
        structured_reference="",
        remittance_text="",
        source_format="camt053",
        is_reversal=False,
    )
    defaults.update(overrides)
    return bank_transaction_repo.insert_transaction(db, **defaults)


def test_create_batch_and_insert_transaction_round_trip(db):
    batch_id = _batch(db)
    tx_id = _insert(db, batch_id)

    tx = bank_transaction_repo.get(db, tx_id)
    assert tx.bank_import_batch_id == batch_id
    assert tx.bank_reference == "REF-001"
    assert tx.amount_rappen == 10_000
    assert tx.credit_debit_indicator == "CRDT"
    assert tx.status == "unmatched"
    assert tx.is_reversal is False
    assert tx.matched_person_id is None
    assert tx.account_entry_id is None


def test_reimporting_the_same_reference_amount_and_direction_is_a_no_op(db):
    """The idempotency key is (bank_reference, currency, amount_rappen,
    credit_debit_indicator) -- an exact duplicate must not create a
    second row, and insert_transaction must report it as skipped (`None`)."""
    batch_1 = _batch(db, "auszug1.xml")
    first_id = _insert(db, batch_1)

    batch_2 = _batch(db, "auszug2.xml")
    second_id = _insert(db, batch_2)

    assert first_id is not None
    assert second_id is None
    all_with_ref = db.execute(
        "SELECT COUNT(*) AS n FROM bank_transactions WHERE bank_reference = 'REF-001'"
    ).fetchone()["n"]
    assert all_with_ref == 1


def test_two_real_double_payments_with_different_references_both_stored(db):
    """Two genuinely different bank transactions (different bank
    references) for the same amount/direction must both be stored --
    this is the "person pays the same invoice twice" scenario, which must
    never be blocked by the dedup constraint."""
    batch_id = _batch(db)
    first_id = _insert(db, batch_id, bank_reference="REF-001")
    second_id = _insert(db, batch_id, bank_reference="REF-002")

    assert first_id is not None
    assert second_id is not None
    assert first_id != second_id


def test_list_open_returns_only_unmatched_and_suggested(db):
    batch_id = _batch(db)
    open_id = _insert(db, batch_id, bank_reference="REF-OPEN")
    ignored_id = _insert(db, batch_id, bank_reference="REF-IGNORED")
    bank_transaction_repo.set_status(db, ignored_id, "ignored")

    open_ids = [tx.id for tx in bank_transaction_repo.list_open(db)]
    assert open_id in open_ids
    assert ignored_id not in open_ids


def test_set_match_records_person_and_account_entry(db):
    batch_id = _batch(db)
    tx_id = _insert(db, batch_id)
    person_id = _person(db)
    entry_id = account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-10_000, booked_at="2026-01-15",
    )

    bank_transaction_repo.set_match(
        db, tx_id, status="auto_matched", matched_person_id=person_id, account_entry_id=entry_id
    )

    tx = bank_transaction_repo.get(db, tx_id)
    assert tx.status == "auto_matched"
    assert tx.matched_person_id == person_id
    assert tx.account_entry_id == entry_id


def test_clear_match_resets_status_and_match_fields(db):
    batch_id = _batch(db)
    tx_id = _insert(db, batch_id)
    person_id = _person(db)
    entry_id = account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-10_000, booked_at="2026-01-15",
    )
    bank_transaction_repo.set_match(
        db, tx_id, status="manually_matched", matched_person_id=person_id, account_entry_id=entry_id
    )

    bank_transaction_repo.clear_match(db, tx_id, status="unmatched")

    tx = bank_transaction_repo.get(db, tx_id)
    assert tx.status == "unmatched"
    assert tx.matched_person_id is None
    assert tx.account_entry_id is None


def test_is_reversal_round_trips_true(db):
    batch_id = _batch(db)
    tx_id = _insert(db, batch_id, is_reversal=True)

    assert bank_transaction_repo.get(db, tx_id).is_reversal is True
