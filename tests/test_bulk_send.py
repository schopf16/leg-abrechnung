"""Tests for app.emailing.bulk_send (recipient resolution and send
orchestration -- graph_client is mocked throughout, no real network calls
and no real SMTP/Graph server involved)."""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.emailing import bulk_send
from app.emailing.bulk_send import (
    EmailSendResult,
    list_broadcast_recipients,
    list_leg_recipients,
    resend_invoice_email,
    send_broadcast_email,
    send_invoice_emails,
)
from app.emailing.graph_client import GraphApiError, GraphAuthError
from app.models import billing_run as billing_run_repo
from app.models import email_log as email_log_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import zuordnung as zuordnung_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, MeteringPoint
from app.models.person import Person
from app.models.site import Site
from app.models.zuordnung import Zuordnung


def _person(db, name: str = "P", email: str = "p@example.invalid",
            papierrechnung: bool = False) -> int:
    """Create a Person (fantasy email, never a real address) and return its id.

    Always created active -- `person_repo.create` ignores any `aktiv`
    value and always creates active; use `person_repo.set_aktiv` afterwards
    to deactivate.
    """
    return person_repo.create(
        db,
        Person(
            id=None, anrede="Frau", firma="", vorname=name, nachname="Test",
            kontakt_email=email, kontakt_telefon="",
            rechnungsadresse_strasse="", rechnungsadresse_hausnummer="", rechnungsadresse_plz="",
            rechnungsadresse_ort="", rechnungsadresse_land="CH",
            iban="", kundennummer=None, bkw_kundennummer=None,
            papierrechnung=papierrechnung, aktiv=True, created_at="",
        ),
    )


def _site(db) -> int:
    return site_repo.create(
        db,
        Site(
            id=None, street="Testweg", house_number="1", postal_code="3000", municipality="Bern", address_detail="",
            substation_area_id=None, created_at="",
        ),
    )


def _leg(db, name: str = "LEG Test") -> int:
    return leg_repo.create(db, Leg(id=None, name=name, note="", created_at=""))


def _metering_point(db, designation: str, site_id: int, leg_id) -> int:
    return metering_point_repo.create(
        db,
        MeteringPoint(
            id=None, designation=designation, direction=DIRECTION_CONSUMPTION,
            site_id=site_id, leg_id=leg_id, pv_capacity_kwp=None,
            battery_capacity_kwh=None, created_at="",
        ),
    )


def _zuordnung(db, person_id: int, metering_point_id: int, von: date, bis: date | None = None) -> None:
    zuordnung_repo.create(
        db,
        Zuordnung(id=None, person_id=person_id, metering_point_id=metering_point_id,
                   gueltig_von=von, gueltig_bis=bis, created_at=""),
    )


# -- list_broadcast_recipients ------------------------------------------------

def test_list_broadcast_recipients_includes_active_with_email(db):
    person_id = _person(db, "Anna", email="anna@example.invalid")
    recipients = list_broadcast_recipients(db)
    assert [p.id for p in recipients] == [person_id]


def test_list_broadcast_recipients_excludes_inactive(db):
    # Person.create() always creates active (deactivation happens only via
    # set_aktiv, see app.models.person) -- deactivate it afterwards.
    person_id = _person(db, "Inaktiv", email="inaktiv@example.invalid")
    person_repo.set_aktiv(db, person_id, False)
    assert list_broadcast_recipients(db) == []


def test_list_broadcast_recipients_excludes_missing_email(db):
    _person(db, "KeineMail", email="")
    assert list_broadcast_recipients(db) == []


# -- list_leg_recipients -------------------------------------------------------

def test_list_leg_recipients_includes_current_member(db):
    leg_id = _leg(db)
    site_id = _site(db)
    metering_point_id = _metering_point(db, "CH-A", site_id, leg_id)
    person_id = _person(db, "Anna", email="anna@example.invalid")
    _zuordnung(db, person_id, metering_point_id, date(2020, 1, 1))

    recipients = list_leg_recipients(db, leg_id)
    assert [p.id for p in recipients] == [person_id]


def test_list_leg_recipients_includes_not_yet_started_zuordnung(db):
    """An administrator who pre-enters next quarter's move-ins weeks or
    months in advance still gets them included -- this is the behaviour
    real customer data forced: a strict "already started" check had every
    LEG's recipient list come back empty until the Zuordnung's exact start
    date arrived (`Zuordnung.is_current_or_upcoming`, unconditional)."""
    leg_id = _leg(db)
    site_id = _site(db)
    metering_point_id = _metering_point(db, "CH-A", site_id, leg_id)
    person_id = _person(db, "Anna", email="anna@example.invalid")
    _zuordnung(db, person_id, metering_point_id, date.today() + timedelta(days=90))

    recipients = list_leg_recipients(db, leg_id)
    assert [p.id for p in recipients] == [person_id]


def test_list_leg_recipients_excludes_other_leg(db):
    leg_a = _leg(db, "LEG A")
    leg_b = _leg(db, "LEG B")
    site_id = _site(db)
    metering_point_id = _metering_point(db, "CH-A", site_id, leg_a)
    person_id = _person(db, "Anna", email="anna@example.invalid")
    _zuordnung(db, person_id, metering_point_id, date(2020, 1, 1))

    assert list_leg_recipients(db, leg_b) == []


def test_list_leg_recipients_excludes_expired_zuordnung(db):
    leg_id = _leg(db)
    site_id = _site(db)
    metering_point_id = _metering_point(db, "CH-A", site_id, leg_id)
    person_id = _person(db, "Anna", email="anna@example.invalid")
    _zuordnung(db, person_id, metering_point_id, date(2015, 1, 1), date(2016, 1, 1))

    assert list_leg_recipients(db, leg_id) == []


def test_list_leg_recipients_deduplicates_multiple_metering_points(db):
    leg_id = _leg(db)
    site_id = _site(db)
    mp1 = _metering_point(db, "CH-A", site_id, leg_id)
    mp2 = _metering_point(db, "CH-B", site_id, leg_id)
    person_id = _person(db, "Anna", email="anna@example.invalid")
    _zuordnung(db, person_id, mp1, date(2020, 1, 1))
    _zuordnung(db, person_id, mp2, date(2020, 1, 1))

    recipients = list_leg_recipients(db, leg_id)
    assert [p.id for p in recipients] == [person_id]


def test_list_leg_recipients_excludes_missing_email(db):
    leg_id = _leg(db)
    site_id = _site(db)
    metering_point_id = _metering_point(db, "CH-A", site_id, leg_id)
    person_id = _person(db, "KeineMail", email="")
    _zuordnung(db, person_id, metering_point_id, date(2020, 1, 1))

    assert list_leg_recipients(db, leg_id) == []


# -- send_broadcast_email -------------------------------------------------------

def test_send_broadcast_email_sends_individually_and_logs(db):
    person_a = person_repo.get(db, _person(db, "Anna", email="anna@example.invalid"))
    person_b = person_repo.get(db, _person(db, "Beat", email="beat@example.invalid"))

    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(bulk_send.graph_client, "send_email", AsyncMock()) as mock_send:
        result = asyncio.run(
            send_broadcast_email(
                db, "config", [person_a, person_b], "Betreff {vorname}", "Hallo {vorname}",
                scope="alle",
            )
        )

    assert isinstance(result, EmailSendResult)
    assert result.sent == ["Anna Test", "Beat Test"]
    assert mock_send.call_count == 2
    first_call_kwargs = mock_send.call_args_list[0].kwargs
    assert first_call_kwargs["to_address"] == "anna@example.invalid"
    assert first_call_kwargs["subject"] == "Betreff Anna"
    assert first_call_kwargs["body"] == "Hallo Anna"

    log_entries = email_log_repo.list_all(db)
    assert len(log_entries) == 1
    assert sorted(log_entries[0].recipient_emails) == ["anna@example.invalid", "beat@example.invalid"]


def test_send_broadcast_email_continues_after_single_recipient_error(db):
    person_a = person_repo.get(db, _person(db, "Anna", email="anna@example.invalid"))
    person_b = person_repo.get(db, _person(db, "Beat", email="beat@example.invalid"))

    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(
             bulk_send.graph_client, "send_email",
             AsyncMock(side_effect=[GraphApiError("boom"), None]),
         ):
        result = asyncio.run(
            send_broadcast_email(db, "config", [person_a, person_b], "s", "b", scope="alle")
        )

    assert result.sent == ["Beat Test"]
    assert len(result.errors) == 1
    assert "Anna Test" in result.errors[0]
    # Only the successful recipient is recorded in the log.
    assert email_log_repo.list_all(db)[0].recipient_emails == ["beat@example.invalid"]


def test_send_broadcast_email_aborts_on_auth_error(db):
    person_a = person_repo.get(db, _person(db, "Anna", email="anna@example.invalid"))
    person_b = person_repo.get(db, _person(db, "Beat", email="beat@example.invalid"))

    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(
             bulk_send.graph_client, "send_email", AsyncMock(side_effect=GraphAuthError("bad creds"))
         ):
        with pytest.raises(GraphAuthError):
            asyncio.run(send_broadcast_email(db, "config", [person_a, person_b], "s", "b", scope="alle"))


def test_send_broadcast_email_calls_on_progress_per_recipient(db):
    person_a = person_repo.get(db, _person(db, "Anna", email="anna@example.invalid"))
    person_b = person_repo.get(db, _person(db, "Beat", email="beat@example.invalid"))
    progress_calls = []

    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(bulk_send.graph_client, "send_email", AsyncMock()):
        asyncio.run(
            send_broadcast_email(
                db, "config", [person_a, person_b], "s", "b", scope="alle",
                on_progress=lambda done, total: progress_calls.append((done, total)),
            )
        )

    assert progress_calls == [(1, 2), (2, 2)]


def test_send_broadcast_email_passes_attachment_to_every_recipient_and_logs_it(db, tmp_path):
    person_a = person_repo.get(db, _person(db, "Anna", email="anna@example.invalid"))
    person_b = person_repo.get(db, _person(db, "Beat", email="beat@example.invalid"))
    attachment_path = tmp_path / "einladung.pdf"
    attachment_path.write_bytes(b"%PDF-fake-content")

    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(bulk_send.graph_client, "send_email", AsyncMock()) as mock_send:
        asyncio.run(
            send_broadcast_email(
                db, "config", [person_a, person_b], "s", "b", scope="alle",
                attachment_path=attachment_path, attachment_filename="einladung.pdf",
            )
        )

    for call in mock_send.call_args_list:
        assert call.kwargs["attachment_path"] == attachment_path
        assert call.kwargs["attachment_filename"] == "einladung.pdf"
    assert email_log_repo.list_all(db)[0].attachment_filename == "einladung.pdf"


def test_send_broadcast_email_returns_early_for_no_recipients(db):
    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock()) as mock_token:
        result = asyncio.run(send_broadcast_email(db, "config", [], "s", "b", scope="alle"))

    assert result == EmailSendResult()
    mock_token.assert_not_called()
    assert email_log_repo.list_all(db) == []


# -- send_invoice_emails / resend_invoice_email --------------------------------

def _run_with_item(
    db, *, papierrechnung=False, email="anna@example.invalid", pdf_path="rechnung.pdf",
    net_amount_rappen=5000,
) -> tuple[BillingRun, BillingRunItem]:
    """Create a Leg, a Person, a BillingRun and one BillingRunItem for them."""
    leg_id = _leg(db)
    person_id = _person(db, "Anna", email=email, papierrechnung=papierrechnung)
    run_id = billing_run_repo.create_run(
        db, BillingRun(id=None, leg_id=leg_id, period_year=2026, period_quarter=1,
                        created_at="", price_rp_per_kwh=20.0, status="erstellt", notes=""),
    )
    [item_id] = billing_run_repo.add_items(
        db,
        [
            BillingRunItem(
                id=None, billing_run_id=run_id, person_id=person_id,
                consumed_kwh=10.0, produced_kwh=0.0, price_rp_per_kwh=20.0,
                verwaltungsaufwand_bezug_rappen=0, papierrechnung_rappen=0,
                net_amount_rappen=net_amount_rappen, pdf_path=pdf_path, created_at="",
            )
        ],
    )
    run = billing_run_repo.get_run(db, run_id)
    item = billing_run_repo.list_items(db, run_id)[0]
    return run, item


def test_send_invoice_emails_skips_papierrechnung(db):
    run, _ = _run_with_item(db, papierrechnung=True)
    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(bulk_send.graph_client, "send_email", AsyncMock()) as mock_send:
        result = asyncio.run(send_invoice_emails(db, "config", run, "s", "b"))

    assert result.sent == []
    assert "Papierrechnung" in result.skipped[0]
    mock_send.assert_not_called()


def test_send_invoice_emails_skips_missing_email(db):
    run, _ = _run_with_item(db, email="")
    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(bulk_send.graph_client, "send_email", AsyncMock()) as mock_send:
        result = asyncio.run(send_invoice_emails(db, "config", run, "s", "b"))

    assert "E-Mail-Adresse" in result.skipped[0]
    mock_send.assert_not_called()


def test_send_invoice_emails_skips_missing_pdf(db):
    run, _ = _run_with_item(db, pdf_path=None)
    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(bulk_send.graph_client, "send_email", AsyncMock()) as mock_send:
        result = asyncio.run(send_invoice_emails(db, "config", run, "s", "b"))

    assert "PDF" in result.skipped[0]
    mock_send.assert_not_called()


def test_send_invoice_emails_skips_already_sent(db):
    run, item = _run_with_item(db)
    billing_run_repo.set_item_email_sent_at(db, item.id, "2026-01-01T00:00:00+00:00")
    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(bulk_send.graph_client, "send_email", AsyncMock()) as mock_send:
        result = asyncio.run(send_invoice_emails(db, "config", run, "s", "b"))

    assert "bereits" in result.skipped[0]
    mock_send.assert_not_called()


def test_send_invoice_emails_sends_and_records_timestamp(db):
    run, item = _run_with_item(db, net_amount_rappen=4250)
    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(bulk_send.graph_client, "send_email", AsyncMock()) as mock_send:
        result = asyncio.run(
            send_invoice_emails(db, "config", run, "Rechnung {leg}", "Betrag: {betrag}")
        )

    assert result.sent == ["Anna Test"]
    kwargs = mock_send.call_args.kwargs
    assert kwargs["subject"] == "Rechnung LEG Test"
    assert kwargs["body"] == "Betrag: 42.50"
    assert kwargs["attachment_path"].name == "rechnung.pdf"

    updated_item = billing_run_repo.list_items(db, run.id)[0]
    assert updated_item.email_sent_at is not None


def test_send_invoice_emails_continues_after_error(db):
    run, _ = _run_with_item(db)
    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(bulk_send.graph_client, "send_email", AsyncMock(side_effect=GraphApiError("boom"))):
        result = asyncio.run(send_invoice_emails(db, "config", run, "s", "b"))

    assert result.sent == []
    assert len(result.errors) == 1


def test_send_invoice_emails_treats_missing_pdf_file_as_error_not_crash(db, tmp_path):
    """A PDF recorded in pdf_path but since deleted/moved from disk must be
    a clean per-person error, not an unhandled exception that aborts the
    whole batch -- exercises the real graph_client.send_email attachment
    handling (only get_access_token is mocked, no real network call is
    ever reached since the missing file is caught before any HTTP call)."""
    missing_pdf = tmp_path / "verschwunden.pdf"
    run, _ = _run_with_item(db, pdf_path=str(missing_pdf))
    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")):
        result = asyncio.run(send_invoice_emails(db, "config", run, "s", "b"))

    assert result.sent == []
    assert len(result.errors) == 1
    assert "verschwunden.pdf" in result.errors[0]


def test_resend_invoice_email_sends_despite_already_sent(db):
    run, item = _run_with_item(db)
    billing_run_repo.set_item_email_sent_at(db, item.id, "2026-01-01T00:00:00+00:00")
    item = billing_run_repo.list_items(db, run.id)[0]

    with patch.object(bulk_send.graph_client, "get_access_token", AsyncMock(return_value="tok")), \
         patch.object(bulk_send.graph_client, "send_email", AsyncMock()) as mock_send:
        asyncio.run(resend_invoice_email(db, "config", run, item, "s", "b"))

    mock_send.assert_called_once()


def test_resend_invoice_email_raises_if_no_pdf(db):
    run, item = _run_with_item(db, pdf_path=None)
    with pytest.raises(ValueError, match="PDF"):
        asyncio.run(resend_invoice_email(db, "config", run, item, "s", "b"))
