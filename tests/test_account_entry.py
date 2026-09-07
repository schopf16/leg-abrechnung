"""Tests for app.models.account_entry (Debitoren ledger)."""

from app.models import account_entry as account_entry_repo
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.person import Person


def _person(db, name: str = "P", email: str = "p@example.invalid") -> int:
    return person_repo.create(
        db,
        Person(
            id=None, anrede="Frau", firma="", vorname=name, nachname="Test",
            kontakt_email=email, kontakt_telefon="",
            rechnungsadresse_strasse="", rechnungsadresse_hausnummer="", rechnungsadresse_plz="",
            rechnungsadresse_ort="", rechnungsadresse_land="CH",
            iban="", kundennummer=None, bkw_kundennummer=None,
            papierrechnung=False, aktiv=True, created_at="",
        ),
    )


def _leg(db, name: str = "LEG Test") -> int:
    return leg_repo.create(db, Leg(id=None, name=name, bemerkung="", created_at=""))


def _billing_item(db, person_id: int, net_amount_rappen: int) -> tuple[int, int]:
    """Create a minimal billing run (its own, uniquely-named LEG) with one
    item for `person_id`.

    Returns:
        `(billing_run_id, item_id)`.
    """
    leg_id = _leg(db, f"LEG Test {person_id}-{net_amount_rappen}")
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
                verwaltungsaufwand_bezug_rappen=0, papierrechnung_rappen=0,
                net_amount_rappen=net_amount_rappen, pdf_path=None, created_at="",
            )
        ],
    )
    return run_id, item_ids[0]


def test_create_and_list_for_person_round_trip(db):
    person_id = _person(db)
    entry_id = account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-5000,
        booked_at="2026-01-15", note="Testzahlung",
    )

    entries = account_entry_repo.list_for_person(db, person_id)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.id == entry_id
    assert entry.kind == "zahlungseingang"
    assert entry.amount_rappen == -5000
    assert entry.booked_at == "2026-01-15"
    assert entry.note == "Testzahlung"
    assert entry.billing_run_item_id is None
    assert entry.bank_transaction_id is None


def test_saldo_is_zero_with_no_history(db):
    person_id = _person(db)
    assert account_entry_repo.get_saldo_rappen(db, person_id) == 0
    assert account_entry_repo.get_all_saldi(db).get(person_id, 0) == 0


def test_saldo_combines_invoices_and_payments(db):
    """A person owes 10000 Rappen from an invoice, pays 6000 -- Saldo
    should be the remaining 4000 owed to the LEG (positive, internal
    convention)."""
    person_id = _person(db)
    run_id, item_id = _billing_item(db, person_id, net_amount_rappen=10_000)
    account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-6_000,
        booked_at="2026-02-01", billing_run_item_id=item_id,
    )

    assert account_entry_repo.get_saldo_rappen(db, person_id) == 4_000
    assert account_entry_repo.get_all_saldi(db)[person_id] == 4_000


def test_saldo_reflects_overpayment_as_negative_internal_value(db):
    """Paying more than invoiced must not be special-cased -- the excess
    simply shows as a negative (LEG-owes-person) internal Saldo."""
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, net_amount_rappen=10_000)
    account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-15_000,
        booked_at="2026-02-01", billing_run_item_id=item_id,
    )

    assert account_entry_repo.get_saldo_rappen(db, person_id) == -5_000


def test_double_payment_of_the_same_invoice_is_never_blocked(db):
    """Two separate incoming payments against the same billing_run_item_id
    must both be recorded -- there is deliberately no "one payment per
    invoice" constraint (see the Debitoren plan's explicit scenario)."""
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, net_amount_rappen=10_000)
    account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-10_000,
        booked_at="2026-02-01", billing_run_item_id=item_id,
    )
    account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-10_000,
        booked_at="2026-02-05", billing_run_item_id=item_id,
    )

    assert len(account_entry_repo.list_for_person(db, person_id)) == 2
    assert account_entry_repo.get_saldo_rappen(db, person_id) == -10_000


def test_payout_reduces_a_negative_saldo_back_toward_zero(db):
    """The LEG owes the person 8000 (credit); executing the payout must
    move the Saldo back to 0, so a payout is recorded POSITIVE."""
    person_id = _person(db)
    _run_id, _item_id = _billing_item(db, person_id, net_amount_rappen=-8_000)
    assert account_entry_repo.get_saldo_rappen(db, person_id) == -8_000

    account_entry_repo.create(
        db, person_id=person_id, kind="auszahlung", amount_rappen=8_000,
        booked_at="2026-02-01",
    )

    assert account_entry_repo.get_saldo_rappen(db, person_id) == 0


def test_delete_removes_an_entry_and_updates_saldo(db):
    person_id = _person(db)
    entry_id = account_entry_repo.create(
        db, person_id=person_id, kind="korrektur", amount_rappen=1_000, booked_at="2026-01-01",
    )
    assert account_entry_repo.get_saldo_rappen(db, person_id) == 1_000

    account_entry_repo.delete(db, entry_id)

    assert account_entry_repo.list_for_person(db, person_id) == []
    assert account_entry_repo.get_saldo_rappen(db, person_id) == 0


def test_get_all_saldi_covers_multiple_persons_independently(db):
    person_a = _person(db, "A", "a@example.invalid")
    person_b = _person(db, "B", "b@example.invalid")
    _billing_item(db, person_a, net_amount_rappen=5_000)
    _billing_item(db, person_b, net_amount_rappen=-2_000)

    saldi = account_entry_repo.get_all_saldi(db)

    assert saldi[person_a] == 5_000
    assert saldi[person_b] == -2_000


def test_get_remaining_for_item_with_no_payments_is_the_full_amount(db):
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, net_amount_rappen=10_000)

    assert account_entry_repo.get_remaining_for_item(db, item_id, 10_000) == 10_000


def test_get_remaining_for_item_subtracts_only_payments_linked_to_it(db):
    """A payment linked to a *different* item must not reduce this item's
    own remaining amount, even for the same person -- see Finding #1 of
    the review this fixes (a Mahnung's QR-bill must never overcharge for
    an item already partially covered)."""
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, net_amount_rappen=10_000)
    _run_id_2, other_item_id = _billing_item(db, person_id, net_amount_rappen=5_000)
    account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-3_000,
        booked_at="2026-02-01", billing_run_item_id=item_id,
    )
    account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-5_000,
        booked_at="2026-02-01", billing_run_item_id=other_item_id,
    )

    assert account_entry_repo.get_remaining_for_item(db, item_id, 10_000) == 7_000
    assert account_entry_repo.get_remaining_for_item(db, other_item_id, 5_000) == 0


def test_get_remaining_for_item_never_goes_negative(db):
    """An overshooting payment against one specific item becomes a general
    credit on the person's Saldo, never a negative 'remaining' here."""
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, net_amount_rappen=10_000)
    account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-15_000,
        booked_at="2026-02-01", billing_run_item_id=item_id,
    )

    assert account_entry_repo.get_remaining_for_item(db, item_id, 10_000) == 0


def test_create_with_commit_false_is_visible_within_the_same_connection(db):
    """`commit=False` must still make the row visible to further reads on
    the same connection -- only the fsync-to-disk is deferred, not the
    write itself (see Finding #12: bulk bank-import booking commits once
    per connection_scope instead of once per row)."""
    person_id = _person(db)
    account_entry_repo.create(
        db, person_id=person_id, kind="korrektur", amount_rappen=1_000,
        booked_at="2026-01-01", commit=False,
    )

    assert account_entry_repo.get_saldo_rappen(db, person_id) == 1_000
    db.commit()
