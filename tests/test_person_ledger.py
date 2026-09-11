"""Tests for app.domain.person_ledger (the receivables detail timeline)."""

from datetime import date, datetime, timezone

from app.domain import person_ledger
from app.models import account_entry as account_entry_repo
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import dunning_log as dunning_log_repo
from app.models import person as person_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.person import Person
from app.pdf.person_bill_pdf import PAYMENT_TERM


def _person(db, name: str = "P") -> int:
    return person_repo.create(
        db,
        Person(
            id=None, salutation="Frau", company="", first_name=name, last_name="Muster",
            contact_email="p@example.invalid", contact_phone="",
            billing_street="", billing_house_number="", billing_postal_code="",
            billing_city="", billing_country="CH",
            iban="", customer_number=None, bkw_customer_number=None,
            paper_invoice=False, active=True, created_at="",
        ),
    )


def _billing_item(db, person_id: int, net_amount_rappen: int, *, due_date: str | None = None) -> tuple[int, int]:
    leg_id = leg_repo.create(
        db, Leg(id=None, name=f"LEG {person_id}-{net_amount_rappen}-{id(object())}", note="", created_at="")
    )
    run_id = billing_run_repo.create_run(
        db,
        BillingRun(
            id=None, leg_id=leg_id, period_year=2026, period_quarter=2,
            created_at="2026-01-01T00:00:00", price_rp_per_kwh=20.0, status="created", notes="",
        ),
    )
    item_ids = billing_run_repo.add_items(
        db,
        [
            BillingRunItem(
                id=None, billing_run_id=run_id, person_id=person_id,
                consumed_kwh=10.0, produced_kwh=0.0, price_rp_per_kwh=20.0,
                admin_fee_consumption_rappen=0, paper_invoice_rappen=0,
                net_amount_rappen=net_amount_rappen, pdf_path=None, created_at="2026-01-01T00:00:00",
            )
        ],
    )
    item_id = item_ids[0]
    if due_date is not None:
        billing_run_repo.set_item_due_date(db, item_id, due_date)
    return run_id, item_id


def test_invoice_entry_uses_due_date_minus_payment_term_as_issue_date(db):
    person_id = _person(db)
    due = date.today()
    _run_id, item_id = _billing_item(db, person_id, 10_000, due_date=due.isoformat())

    entries = person_ledger.list_ledger_entries(db, person_id)

    assert len(entries) == 1
    assert entries[0].kind == "invoice"
    assert entries[0].entry_date == (due - PAYMENT_TERM).isoformat()
    assert entries[0].amount_rappen == 10_000
    assert entries[0].billing_run_item_id == item_id


def test_invoice_entry_falls_back_to_created_at_without_due_date(db):
    """`add_items` always stamps `created_at` with the real current time
    (ignoring whatever the caller passed), so this can only be checked
    against today's date, not a fixed one."""
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, 10_000, due_date=None)

    entries = person_ledger.list_ledger_entries(db, person_id)

    # `created_at` is stamped in UTC, and the ledger slices its date part
    # verbatim -- so compare against the UTC date, not the local one, or
    # this fails between local midnight and 02:00 in Switzerland.
    assert entries[0].entry_date == datetime.now(timezone.utc).date().isoformat()


def test_credit_item_is_labeled_credit_note(db):
    person_id = _person(db)
    _billing_item(db, person_id, -5_000, due_date=date.today().isoformat())

    entries = person_ledger.list_ledger_entries(db, person_id)

    assert entries[0].kind == "credit_note"
    assert "Gutschrift" in entries[0].description
    assert entries[0].amount_rappen == -5_000


def test_dunning_entry_has_no_amount_and_describes_stage(db):
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, 10_000)
    dunning_log_repo.create(
        db, person_id=person_id, level=1, amount_rappen=10_000, billing_run_item_ids=[item_id]
    )

    entries = person_ledger.list_ledger_entries(db, person_id)
    dunning_entries = [e for e in entries if e.kind == "dunning"]

    assert len(dunning_entries) == 1
    assert dunning_entries[0].amount_rappen is None
    assert "1. Mahnung" in dunning_entries[0].description
    assert "100.00" in dunning_entries[0].description


def test_account_entry_kinds_are_labeled_and_carry_note(db):
    person_id = _person(db)
    account_entry_repo.create(
        db, person_id=person_id, kind="payment_received", amount_rappen=-10_000,
        booked_at="2026-03-01", note="",
    )
    account_entry_repo.create(
        db, person_id=person_id, kind="correction", amount_rappen=500,
        booked_at="2026-03-05", note="Rundungsdifferenz",
    )

    entries = person_ledger.list_ledger_entries(db, person_id)

    payment = next(e for e in entries if e.kind == "payment_received")
    assert payment.description == "Zahlungseingang"
    assert payment.amount_rappen == -10_000

    korrektur = next(e for e in entries if e.kind == "correction")
    assert korrektur.description == "Korrektur (Rundungsdifferenz)"
    assert korrektur.amount_rappen == 500


def test_entries_are_sorted_chronologically_across_all_sources(db):
    """`dunning_log.create` always stamps `sent_at` with the real current
    time (not controllable via a parameter), so only the relative order
    of the invoice and the payment -- both fully controlled here -- is
    asserted directly; the overall sortedness check still covers the
    dunning notice entry too, wherever "today" happens to place it."""
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, 10_000, due_date="2026-05-01")
    # Issue date = due_date - PAYMENT_TERM (45 days) = 2026-03-17.
    dunning_log_repo.create(
        db, person_id=person_id, level=1, amount_rappen=10_000, billing_run_item_ids=[item_id]
    )
    account_entry_repo.create(
        db, person_id=person_id, kind="payment_received", amount_rappen=-10_000, booked_at="2026-12-01",
    )

    entries = person_ledger.list_ledger_entries(db, person_id)

    assert [e.entry_date for e in entries] == sorted(e.entry_date for e in entries)
    assert entries[0].kind == "invoice"
    assert entries[-1].kind == "payment_received"


def test_payment_links_back_to_the_invoice_it_covers(db):
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, 10_000)
    account_entry_repo.create(
        db, person_id=person_id, kind="payment_received", amount_rappen=-10_000,
        booked_at="2026-04-01", billing_run_item_id=item_id,
    )

    entries = person_ledger.list_ledger_entries(db, person_id)
    payment = next(e for e in entries if e.kind == "payment_received")

    assert payment.billing_run_item_id == item_id


def test_no_history_returns_empty_list(db):
    person_id = _person(db)
    assert person_ledger.list_ledger_entries(db, person_id) == []
