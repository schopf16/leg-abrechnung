"""Tests for app.domain.person_ledger (the Debitoren detail timeline)."""

from datetime import date, timedelta

from app.domain import person_ledger
from app.models import account_entry as account_entry_repo
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import mahnung_log as mahnung_log_repo
from app.models import person as person_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.person import Person
from app.pdf.person_bill_pdf import PAYMENT_TERM


def _person(db, name: str = "P") -> int:
    return person_repo.create(
        db,
        Person(
            id=None, anrede="Frau", firma="", vorname=name, nachname="Muster",
            kontakt_email="p@example.invalid", kontakt_telefon="",
            rechnungsadresse_strasse="", rechnungsadresse_hausnummer="", rechnungsadresse_plz="",
            rechnungsadresse_ort="", rechnungsadresse_land="CH",
            iban="", kundennummer=None, bkw_kundennummer=None,
            papierrechnung=False, aktiv=True, created_at="",
        ),
    )


def _billing_item(db, person_id: int, net_amount_rappen: int, *, faellig_am: str | None = None) -> tuple[int, int]:
    leg_id = leg_repo.create(
        db, Leg(id=None, name=f"LEG {person_id}-{net_amount_rappen}-{id(object())}", note="", created_at="")
    )
    run_id = billing_run_repo.create_run(
        db,
        BillingRun(
            id=None, leg_id=leg_id, period_year=2026, period_quarter=2,
            created_at="2026-01-01T00:00:00", price_rp_per_kwh=20.0, status="erstellt", notes="",
        ),
    )
    item_ids = billing_run_repo.add_items(
        db,
        [
            BillingRunItem(
                id=None, billing_run_id=run_id, person_id=person_id,
                consumed_kwh=10.0, produced_kwh=0.0, price_rp_per_kwh=20.0,
                verwaltungsaufwand_bezug_rappen=0, papierrechnung_rappen=0,
                net_amount_rappen=net_amount_rappen, pdf_path=None, created_at="2026-01-01T00:00:00",
            )
        ],
    )
    item_id = item_ids[0]
    if faellig_am is not None:
        billing_run_repo.set_item_faellig_am(db, item_id, faellig_am)
    return run_id, item_id


def test_invoice_entry_uses_faellig_am_minus_payment_term_as_issue_date(db):
    person_id = _person(db)
    faellig = date.today()
    _run_id, item_id = _billing_item(db, person_id, 10_000, faellig_am=faellig.isoformat())

    entries = person_ledger.list_ledger_entries(db, person_id)

    assert len(entries) == 1
    assert entries[0].kind == "rechnung"
    assert entries[0].entry_date == (faellig - PAYMENT_TERM).isoformat()
    assert entries[0].amount_rappen == 10_000
    assert entries[0].billing_run_item_id == item_id


def test_invoice_entry_falls_back_to_created_at_without_faellig_am(db):
    """`add_items` always stamps `created_at` with the real current time
    (ignoring whatever the caller passed), so this can only be checked
    against today's date, not a fixed one."""
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, 10_000, faellig_am=None)

    entries = person_ledger.list_ledger_entries(db, person_id)

    assert entries[0].entry_date == date.today().isoformat()


def test_credit_item_is_labeled_gutschrift(db):
    person_id = _person(db)
    _billing_item(db, person_id, -5_000, faellig_am=date.today().isoformat())

    entries = person_ledger.list_ledger_entries(db, person_id)

    assert entries[0].kind == "gutschrift"
    assert "Gutschrift" in entries[0].description
    assert entries[0].amount_rappen == -5_000


def test_mahnung_entry_has_no_amount_and_describes_stage(db):
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, 10_000)
    mahnung_log_repo.create(
        db, person_id=person_id, stufe=1, betrag_rappen=10_000, billing_run_item_ids=[item_id]
    )

    entries = person_ledger.list_ledger_entries(db, person_id)
    mahnung_entries = [e for e in entries if e.kind == "mahnung"]

    assert len(mahnung_entries) == 1
    assert mahnung_entries[0].amount_rappen is None
    assert "1. Mahnung" in mahnung_entries[0].description
    assert "100.00" in mahnung_entries[0].description


def test_account_entry_kinds_are_labeled_and_carry_note(db):
    person_id = _person(db)
    account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-10_000,
        booked_at="2026-03-01", note="",
    )
    account_entry_repo.create(
        db, person_id=person_id, kind="korrektur", amount_rappen=500,
        booked_at="2026-03-05", note="Rundungsdifferenz",
    )

    entries = person_ledger.list_ledger_entries(db, person_id)

    payment = next(e for e in entries if e.kind == "zahlungseingang")
    assert payment.description == "Zahlungseingang"
    assert payment.amount_rappen == -10_000

    korrektur = next(e for e in entries if e.kind == "korrektur")
    assert korrektur.description == "Korrektur (Rundungsdifferenz)"
    assert korrektur.amount_rappen == 500


def test_entries_are_sorted_chronologically_across_all_sources(db):
    """`mahnung_log.create` always stamps `sent_at` with the real current
    time (not controllable via a parameter), so only the relative order
    of the invoice and the payment -- both fully controlled here -- is
    asserted directly; the overall sortedness check still covers the
    Mahnung entry too, wherever "today" happens to place it."""
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, 10_000, faellig_am="2026-05-01")
    # Issue date = faellig_am - PAYMENT_TERM (45 days) = 2026-03-17.
    mahnung_log_repo.create(
        db, person_id=person_id, stufe=1, betrag_rappen=10_000, billing_run_item_ids=[item_id]
    )
    account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-10_000, booked_at="2026-12-01",
    )

    entries = person_ledger.list_ledger_entries(db, person_id)

    assert [e.entry_date for e in entries] == sorted(e.entry_date for e in entries)
    assert entries[0].kind == "rechnung"
    assert entries[-1].kind == "zahlungseingang"


def test_payment_links_back_to_the_invoice_it_covers(db):
    person_id = _person(db)
    _run_id, item_id = _billing_item(db, person_id, 10_000)
    account_entry_repo.create(
        db, person_id=person_id, kind="zahlungseingang", amount_rappen=-10_000,
        booked_at="2026-04-01", billing_run_item_id=item_id,
    )

    entries = person_ledger.list_ledger_entries(db, person_id)
    payment = next(e for e in entries if e.kind == "zahlungseingang")

    assert payment.billing_run_item_id == item_id


def test_no_history_returns_empty_list(db):
    person_id = _person(db)
    assert person_ledger.list_ledger_entries(db, person_id) == []
