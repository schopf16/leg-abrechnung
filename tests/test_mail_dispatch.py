"""Tests for app.emailing.mail: the config's type decides Graph or SMTP, and SMTP errors behave like Graph's."""

import asyncio
import smtplib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import GraphConfig, SmtpConfig
from app.emailing import graph_client, mail, smtp_client
from app.emailing.bulk_send import send_broadcast_email
from app.emailing.errors import MailAuthError, MailError
from app.models import email_log as email_log_repo
from app.models import person as person_repo
from app.models.person import Person

GRAPH = GraphConfig("tenant", "client", "secret", "leg@example.invalid", "LEG")
SMTP = SmtpConfig(
    host="mail.example.invalid",
    port=465,
    security="ssl",
    username="info@example.invalid",
    password="geheim",
    sender_address="info@example.invalid",
    sender_name="Sunnesyte",
    send_delay_seconds=0.0,
    imap_enabled=False,
    imap_host="mail.example.invalid",
    imap_port=993,
    imap_sent_folder="",
)
MESSAGE = {"to_addresses": ["a@example.invalid"], "to_name": "A", "subject": "s", "body": "b"}


def test_graph_errors_are_mail_errors_so_callers_catch_one_family():
    assert issubclass(graph_client.GraphAuthError, MailAuthError)
    assert issubclass(graph_client.GraphApiError, MailError)
    assert issubclass(MailAuthError, MailError)


def test_graph_config_goes_via_graph_with_its_token():
    with (
        patch.object(graph_client, "get_access_token", AsyncMock(return_value="tok")),
        patch.object(graph_client, "send_email", AsyncMock()) as graph_send,
        patch.object(smtp_client, "send_email", AsyncMock()) as smtp_send,
    ):
        token = asyncio.run(mail.prepare(GRAPH))
        asyncio.run(mail.send_email(GRAPH, token, **MESSAGE))
    assert graph_send.await_args.args == (GRAPH, "tok")
    smtp_send.assert_not_awaited()


def test_smtp_config_goes_via_smtp_and_needs_no_token():
    with (
        patch.object(graph_client, "get_access_token", AsyncMock()) as graph_token,
        patch.object(graph_client, "send_email", AsyncMock()) as graph_send,
        patch.object(smtp_client, "send_email", AsyncMock()) as smtp_send,
    ):
        token = asyncio.run(mail.prepare(SMTP))
        asyncio.run(mail.send_email(SMTP, token, **MESSAGE))
    assert token == ""
    graph_token.assert_not_awaited()
    graph_send.assert_not_awaited()
    assert smtp_send.await_args.args == (SMTP,)
    assert smtp_send.await_args.kwargs["to_addresses"] == ["a@example.invalid"]


def test_check_connection_per_transport():
    with (
        patch.object(graph_client, "get_access_token", AsyncMock(return_value="tok")),
        patch.object(smtp_client, "check_connection", AsyncMock(return_value="SMTP ok")),
    ):
        assert "leg@example.invalid" in asyncio.run(mail.check_connection(GRAPH))
        assert asyncio.run(mail.check_connection(SMTP)) == "SMTP ok"


def test_an_unknown_config_is_a_programming_error_not_a_mail_failure():
    with pytest.raises(TypeError):
        asyncio.run(mail.prepare("config"))


def _people(db) -> list[Person]:
    people = []
    for first, email in (("Anna", "anna@example.invalid"), ("Beat", "beat@example.invalid")):
        person_id = person_repo.create(
            db,
            Person(
                id=None,
                salutation="",
                company="",
                first_name=first,
                last_name="Test",
                contact_email=email,
                contact_phone="",
                billing_street="Weg",
                billing_house_number="1",
                billing_postal_code="3000",
                billing_city="Bern",
                billing_country="CH",
                iban="",
                customer_number=None,
                bkw_customer_number=None,
                paper_invoice=False,
                active=True,
                created_at="2026-01-01",
            ),
        )
        people.append(person_repo.get(db, person_id))
    return people


def test_smtp_batch_skips_a_refused_recipient_and_logs_only_the_sent_one(db):
    """The same contract as with Graph: one bad address does not stop the others."""
    server = MagicMock()
    server.send_message.side_effect = [
        smtplib.SMTPRecipientsRefused({"anna@example.invalid": (550, b"User unknown")}),
        {},
    ]
    with patch.object(smtp_client, "_open_smtp", return_value=server):
        result = asyncio.run(send_broadcast_email(db, SMTP, _people(db), "s", "b", scope="all"))
    assert result.sent == ["Beat Test"]
    assert "Anna Test" in result.errors[0] and "User unknown" in result.errors[0]
    assert email_log_repo.list_all(db)[0].recipient_emails == ["beat@example.invalid"]


def test_smtp_batch_stops_at_a_wrong_password(db):
    """Also as with Graph: wrong credentials fail for everyone, so the batch aborts at once."""
    with patch.object(
        smtp_client, "_send_sync", side_effect=MailAuthError("Anmeldung am SMTP-Server fehlgeschlagen")
    ) as send:
        with pytest.raises(MailAuthError):
            asyncio.run(send_broadcast_email(db, SMTP, _people(db), "s", "b", scope="all"))
    assert send.call_count == 1
    assert email_log_repo.list_all(db) == []


def test_smtp_batch_survives_a_broken_stored_address(db):
    """One umlaut in one stored address must not stop the invoices of everybody else."""
    anna, beat = _people(db)
    anna.contact_email = "änna@example.invalid"
    person_repo.update(db, anna)
    server = MagicMock()
    server.send_message.return_value = {}
    with patch.object(smtp_client, "_open_smtp", return_value=server):
        result = asyncio.run(
            send_broadcast_email(db, SMTP, [person_repo.get(db, anna.id), beat], "s", "b", scope="all")
        )
    assert result.sent == ["Beat Test"]
    assert "Ungültige E-Mail-Adresse" in result.errors[0]
    assert server.send_message.call_count == 1
