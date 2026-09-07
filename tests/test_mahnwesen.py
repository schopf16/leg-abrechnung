"""Tests for app.domain.mahnwesen (escalation detection and sending)."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.domain import mahnwesen
from app.models import account_entry as account_entry_repo
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import mahnung_log as mahnung_log_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.person import Person
from app.pdf import mahnung_pdf


def _person(db, name: str = "P", email: str = "p@example.invalid", papierrechnung: bool = False) -> "Person":
    person_id = person_repo.create(
        db,
        Person(
            id=None, anrede="Frau", firma="", vorname=name, nachname="Muster",
            kontakt_email=email, kontakt_telefon="",
            rechnungsadresse_strasse="Weg", rechnungsadresse_hausnummer="1", rechnungsadresse_plz="3000",
            rechnungsadresse_ort="Bern", rechnungsadresse_land="CH",
            iban="", kundennummer=None, bkw_kundennummer=None,
            papierrechnung=papierrechnung, aktiv=True, created_at="",
        ),
    )
    return person_repo.get(db, person_id)


def _billing_item(
    db, person_id: int, net_amount_rappen: int, *,
    faellig_am: str | None = None, mahnstufe: int = 0, letzte_mahnung_am: str | None = None,
) -> "BillingRunItem":
    leg_id = leg_repo.create(
        db, Leg(id=None, name=f"LEG {person_id}-{net_amount_rappen}-{id(object())}", bemerkung="", created_at="")
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
                verwaltungsaufwand_bezug_rappen=0, papierrechnung_rappen=0,
                net_amount_rappen=net_amount_rappen, pdf_path=None, created_at="",
            )
        ],
    )
    item_id = item_ids[0]
    if faellig_am is not None:
        billing_run_repo.set_item_faellig_am(db, item_id, faellig_am)
    if mahnstufe:
        billing_run_repo.set_item_mahnstufe(db, item_id, mahnstufe, letzte_mahnung_am or datetime.now(timezone.utc).isoformat())
    return billing_run_repo.get_item(db, item_id)


def test_item_with_no_faellig_am_never_escalates(db):
    person = _person(db)
    _billing_item(db, person.id, 10_000, faellig_am=None)

    assert mahnwesen.list_faellige_mahnungen(db) == []


def test_item_not_yet_overdue_does_not_escalate(db):
    person = _person(db)
    _billing_item(db, person.id, 10_000, faellig_am=(date.today() + timedelta(days=5)).isoformat())

    assert mahnwesen.list_faellige_mahnungen(db) == []


def test_overdue_item_at_stufe_0_escalates_to_stufe_1(db):
    person = _person(db)
    _billing_item(db, person.id, 10_000, faellig_am=(date.today() - timedelta(days=1)).isoformat())

    candidates = mahnwesen.list_faellige_mahnungen(db)

    assert len(candidates) == 1
    assert candidates[0].person.id == person.id
    assert candidates[0].stufe == 1


def test_item_at_stufe_1_within_new_deadline_does_not_escalate_further(db):
    person = _person(db)
    _billing_item(
        db, person.id, 10_000,
        faellig_am=(date.today() - timedelta(days=60)).isoformat(),
        mahnstufe=1, letzte_mahnung_am=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    )

    assert mahnwesen.list_faellige_mahnungen(db) == []


def test_item_at_stufe_1_past_new_deadline_escalates_to_stufe_2(db):
    person = _person(db)
    settings = settings_repo.get_settings(db)
    _billing_item(
        db, person.id, 10_000,
        faellig_am=(date.today() - timedelta(days=60)).isoformat(),
        mahnstufe=1,
        letzte_mahnung_am=(datetime.now(timezone.utc) - timedelta(days=settings.mahnung_neue_frist_tage + 1)).isoformat(),
    )

    candidates = mahnwesen.list_faellige_mahnungen(db)

    assert len(candidates) == 1
    assert candidates[0].stufe == 2


def test_item_already_at_stufe_2_never_escalates_further_automatically(db):
    person = _person(db)
    _billing_item(
        db, person.id, 10_000,
        faellig_am=(date.today() - timedelta(days=200)).isoformat(),
        mahnstufe=2, letzte_mahnung_am=(datetime.now(timezone.utc) - timedelta(days=200)).isoformat(),
    )

    assert mahnwesen.list_faellige_mahnungen(db) == []


def test_payment_covering_the_saldo_stops_escalation_even_if_item_itself_looks_unpaid(db):
    """The person-level Saldo gate: a payment recorded elsewhere on the
    account (not necessarily linked to this exact item) must still stop
    the Mahnung, since overall nothing more is owed."""
    person = _person(db)
    item = _billing_item(db, person.id, 10_000, faellig_am=(date.today() - timedelta(days=1)).isoformat())
    account_entry_repo.create(
        db, person_id=person.id, kind="zahlungseingang", amount_rappen=-10_000, booked_at=date.today().isoformat(),
    )

    assert mahnwesen.list_faellige_mahnungen(db) == []


def test_amount_below_bagatellgrenze_is_not_mahned(db):
    person = _person(db)
    settings = settings_repo.get_settings(db)
    tiny_amount = settings.mahnung_bagatellgrenze_rappen - 1
    _billing_item(db, person.id, tiny_amount, faellig_am=(date.today() - timedelta(days=1)).isoformat())

    assert mahnwesen.list_faellige_mahnungen(db) == []


def test_multiple_overdue_items_for_one_person_are_consolidated(db):
    person = _person(db)
    _billing_item(db, person.id, 5_000, faellig_am=(date.today() - timedelta(days=1)).isoformat())
    _billing_item(db, person.id, 3_000, faellig_am=(date.today() - timedelta(days=2)).isoformat())

    candidates = mahnwesen.list_faellige_mahnungen(db)

    assert len(candidates) == 1
    assert len(candidates[0].items) == 2
    assert candidates[0].total_open_rappen == 8_000


def test_send_mahnung_sends_email_with_pdf_attachment_and_advances_stufe(db, tmp_path, monkeypatch):
    monkeypatch.setattr(mahnwesen, "OUTPUT_DIR", tmp_path)
    person = _person(db)
    settings = settings_repo.get_settings(db)
    settings.address_street, settings.address_zip, settings.address_city = "Weg 1", "3000", "Bern"
    settings.qr_iban = "CH4431999123000889012"
    settings_repo.update_settings(db, settings)
    item = _billing_item(db, person.id, 10_000, faellig_am=(date.today() - timedelta(days=1)).isoformat())
    candidate = mahnwesen.list_faellige_mahnungen(db)[0]

    with patch.object(mahnwesen.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(mahnwesen.graph_client, "send_email", AsyncMock()) as mock_send:
        pdf_path = asyncio.run(mahnwesen.send_mahnung(db, config=object(), candidate=candidate))

    assert pdf_path.exists()
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert kwargs["to_address"] == person.kontakt_email
    assert kwargs["attachment_path"] == pdf_path

    updated_item = billing_run_repo.get_item(db, item.id)
    assert updated_item.mahnstufe == 1
    assert updated_item.letzte_mahnung_am is not None

    logs = mahnung_log_repo.list_for_person(db, person.id)
    assert len(logs) == 1
    assert logs[0].stufe == 1
    assert logs[0].billing_run_item_ids == [item.id]


def test_send_mahnung_skips_email_for_papierrechnung_person_but_still_generates_pdf(db, tmp_path, monkeypatch):
    monkeypatch.setattr(mahnwesen, "OUTPUT_DIR", tmp_path)
    person = _person(db, papierrechnung=True)
    settings = settings_repo.get_settings(db)
    settings.address_street, settings.address_zip, settings.address_city = "Weg 1", "3000", "Bern"
    settings.qr_iban = "CH4431999123000889012"
    settings_repo.update_settings(db, settings)
    _billing_item(db, person.id, 10_000, faellig_am=(date.today() - timedelta(days=1)).isoformat())
    candidate = mahnwesen.list_faellige_mahnungen(db)[0]

    with patch.object(mahnwesen.graph_client, "send_email", AsyncMock()) as mock_send:
        pdf_path = asyncio.run(mahnwesen.send_mahnung(db, config=None, candidate=candidate))

    assert pdf_path.exists()
    mock_send.assert_not_called()


def test_send_mahnung_qr_bill_amount_excludes_item_linked_payments_already_received(db, tmp_path, monkeypatch):
    """Finding #1: a Mahnung's QR-bill must charge only what remains open
    on that specific item, not the full original invoiced amount, when a
    partial payment was already linked to it (e.g. one of several open
    items on the same person was paid in the meantime, but the person's
    overall Saldo -- which gates whether a Mahnung is sent at all -- is
    still positive because of another, still-fully-open item)."""
    monkeypatch.setattr(mahnwesen, "OUTPUT_DIR", tmp_path)
    person = _person(db)
    settings = settings_repo.get_settings(db)
    settings.address_street, settings.address_zip, settings.address_city = "Weg 1", "3000", "Bern"
    settings.qr_iban = "CH4431999123000889012"
    settings_repo.update_settings(db, settings)
    item = _billing_item(db, person.id, 10_000, faellig_am=(date.today() - timedelta(days=1)).isoformat())
    account_entry_repo.create(
        db, person_id=person.id, kind="zahlungseingang", amount_rappen=-4_000,
        booked_at=date.today().isoformat(), billing_run_item_id=item.id,
    )
    candidate = mahnwesen.list_faellige_mahnungen(db)[0]

    with patch.object(mahnwesen.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(mahnwesen.graph_client, "send_email", AsyncMock()), \
         patch(
             "app.pdf.mahnung_pdf.build_qr_bill", wraps=mahnung_pdf.build_qr_bill
         ) as mock_build:
        asyncio.run(mahnwesen.send_mahnung(db, config=object(), candidate=candidate))

    assert mock_build.call_count == 1
    assert mock_build.call_args.args[3] == Decimal("60.00")


def test_send_mahnung_skips_qr_bill_for_an_item_already_fully_covered(db, tmp_path, monkeypatch):
    """An item whose full amount was already paid specifically against it
    must not get a page/QR-bill at all in the Mahnung -- there is nothing
    left to charge for it (it only still appears here because another,
    still-open item on the same person keeps the overall Saldo positive)."""
    monkeypatch.setattr(mahnwesen, "OUTPUT_DIR", tmp_path)
    person = _person(db)
    settings = settings_repo.get_settings(db)
    settings.address_street, settings.address_zip, settings.address_city = "Weg 1", "3000", "Bern"
    settings.qr_iban = "CH4431999123000889012"
    settings_repo.update_settings(db, settings)
    overdue = date.today() - timedelta(days=1)
    covered_item = _billing_item(db, person.id, 10_000, faellig_am=overdue.isoformat())
    open_item = _billing_item(db, person.id, 5_000, faellig_am=overdue.isoformat())
    account_entry_repo.create(
        db, person_id=person.id, kind="zahlungseingang", amount_rappen=-10_000,
        booked_at=date.today().isoformat(), billing_run_item_id=covered_item.id,
    )
    candidate = mahnwesen.list_faellige_mahnungen(db)[0]
    assert {item.id for item in candidate.items} == {covered_item.id, open_item.id}

    with patch.object(mahnwesen.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(mahnwesen.graph_client, "send_email", AsyncMock()), \
         patch(
             "app.pdf.mahnung_pdf.build_qr_bill", wraps=mahnung_pdf.build_qr_bill
         ) as mock_build:
        asyncio.run(mahnwesen.send_mahnung(db, config=object(), candidate=candidate))

    assert mock_build.call_count == 1
    assert mock_build.call_args.args[3] == Decimal("50.00")
