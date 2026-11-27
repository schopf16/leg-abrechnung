"""Tests for app.emailing.smtp_client -- fake SMTP and IMAP servers, no mail ever leaves the machine."""

import asyncio
import email.policy
import logging
import smtplib
from email import message_from_bytes
from email.header import decode_header, make_header
from unittest.mock import AsyncMock, patch

import pytest

from app.config import SmtpConfig
from app.emailing import smtp_client
from app.emailing.errors import MailAuthError, MailError
from app.emailing.graph_client import MAX_INLINE_ATTACHMENT_BYTES, Attachment
from app.emailing.smtp_client import (
    build_message,
    check_connection,
    find_sent_folder,
    quote_mailbox,
    send_email,
)

CONFIG = SmtpConfig(
    host="mail.example.invalid",
    port=465,
    security="ssl",
    username="info@sunnesyte.invalid",
    password="geheim",
    sender_address="info@sunnesyte.invalid",
    sender_name="Sunnesyte – Verein",
    send_delay_seconds=0.0,
    imap_enabled=True,
    imap_host="mail.example.invalid",
    imap_port=993,
    imap_sent_folder="",
)
STARTTLS = SmtpConfig(**{**CONFIG.__dict__, "security": "starttls", "port": 587})
NO_COPY = SmtpConfig(**{**CONFIG.__dict__, "imap_enabled": False})

LIST_WITH_FLAG = [
    b'(\\HasNoChildren) "/" "INBOX"',
    b'(\\HasNoChildren \\Sent) "/" "Sent"',
    b'(\\HasNoChildren \\Trash) "/" "Trash"',
]


class FakeSmtp:
    """Stands in for smtplib.SMTP / SMTP_SSL and records what was asked of it."""

    instances: list["FakeSmtp"] = []
    login_error: Exception | None = None
    send_error: Exception | None = None
    starttls_error: Exception | None = None
    connect_error: Exception | None = None
    refused: dict = {}

    def __init__(self, host, port, timeout=None, context=None):
        if FakeSmtp.connect_error:
            raise FakeSmtp.connect_error
        self.host, self.port, self.context = host, port, context
        self.calls: list[str] = []
        self.sent = []
        FakeSmtp.instances.append(self)

    def starttls(self, context=None):
        self.calls.append("starttls")
        if FakeSmtp.starttls_error:
            raise FakeSmtp.starttls_error

    def login(self, user, password):
        self.calls.append(f"login {user}")
        if FakeSmtp.login_error:
            raise FakeSmtp.login_error

    def send_message(self, message):
        self.calls.append("send")
        if FakeSmtp.send_error:
            raise FakeSmtp.send_error
        self.sent.append(message)
        return FakeSmtp.refused

    def quit(self):
        self.calls.append("quit")

    def close(self):
        self.calls.append("close")


class FakeImap:
    """Stands in for imaplib.IMAP4_SSL."""

    instances: list["FakeImap"] = []
    list_lines: list = LIST_WITH_FLAG
    login_error: Exception | None = None
    append_status = "OK"

    def __init__(self, host, port, ssl_context=None, timeout=None):
        self.host, self.port, self.ssl_context = host, port, ssl_context
        self.calls: list[str] = []
        self.appended: list[tuple] = []
        FakeImap.instances.append(self)

    def login(self, user, password):
        self.calls.append(f"login {user}")
        if FakeImap.login_error:
            raise FakeImap.login_error

    def list(self):
        self.calls.append("list")
        return "OK", FakeImap.list_lines

    def append(self, mailbox, flags, date_time, message):
        self.appended.append((mailbox, flags, message))
        return FakeImap.append_status, [b"APPEND completed"]

    def logout(self):
        self.calls.append("logout")


@pytest.fixture(autouse=True)
def fake_servers():
    """Fresh fakes per test, and no pause carried over from another test's send."""
    for cls in (FakeSmtp, FakeImap):
        cls.instances = []
    FakeSmtp.login_error = FakeSmtp.send_error = FakeSmtp.starttls_error = FakeSmtp.connect_error = None
    FakeSmtp.refused = {}
    FakeImap.list_lines, FakeImap.login_error, FakeImap.append_status = LIST_WITH_FLAG, None, "OK"
    smtp_client._last_send_at = None
    with (
        patch.object(smtp_client.smtplib, "SMTP_SSL", FakeSmtp),
        patch.object(smtp_client.smtplib, "SMTP", FakeSmtp),
        patch.object(smtp_client.imaplib, "IMAP4_SSL", FakeImap),
    ):
        yield
    smtp_client._last_send_at = None


def _send(config=CONFIG, **overrides):
    kwargs = {
        "to_addresses": ["vreni@example.invalid"],
        "to_name": "Vreni Muster",
        "subject": "Abrechnung",
        "body": "Guten Tag",
    }
    asyncio.run(send_email(config, **{**kwargs, **overrides}))


def _header(message, name: str) -> str:
    return str(make_header(decode_header(message[name])))


# --- The message -------------------------------------------------------------------------------


def test_message_carries_names_both_addresses_of_a_couple_and_a_message_id_on_the_own_domain():
    message = build_message(
        CONFIG,
        to_addresses=["yvonne@example.invalid", "jamie@example.invalid"],
        to_name="Yvonne Christen und Jamie Townsend",
        subject="Abrechnung Q3",
        body="Guten Tag\n\nIhre Abrechnung.",
    )
    assert _header(message, "From") == "Sunnesyte – Verein <info@sunnesyte.invalid>"
    assert "yvonne@example.invalid" in message["To"] and "jamie@example.invalid" in message["To"]
    assert message["Message-ID"].endswith("@sunnesyte.invalid>")
    assert message["Date"]
    assert message.get_content().startswith("Guten Tag")


def test_a_line_break_in_the_subject_cannot_start_a_header_of_its_own():
    message = build_message(
        CONFIG,
        to_addresses=["a@example.invalid"],
        to_name="A\nBcc: x@evil.invalid",
        subject="Hallo\r\nBcc: x@evil.invalid",
        body="b",
    )
    assert message["Bcc"] is None
    assert "\n" not in message["Subject"]


def test_umlauts_travel_7_bit_clean_and_arrive_intact():
    """An 8-bit body needs the server's 8BITMIME, which smtplib never asks for -- so quoted-printable."""
    message = build_message(
        CONFIG,
        to_addresses=["a@example.invalid"],
        to_name="Müller",
        subject="Grüsse",
        body="Grüezi Frau Müller …",
    )
    raw = message.as_bytes()
    assert all(byte < 128 for byte in raw)
    received = message_from_bytes(raw, policy=email.policy.default)
    assert received.get_content().strip() == "Grüezi Frau Müller …"
    assert received["Subject"] == "Grüsse"


@pytest.mark.parametrize("stored", ["a@example.invalid\nBcc: x@evil.invalid", "müller@example.invalid"])
def test_a_broken_stored_address_is_a_mail_error_for_that_recipient_only(stored):
    """Not a ValueError or UnicodeEncodeError, which a batch send does not catch and would abort on."""
    with pytest.raises(MailError, match="Ungültige E-Mail-Adresse") as excinfo:
        build_message(CONFIG, to_addresses=[stored], to_name="X", subject="s", body="b")
    assert not isinstance(excinfo.value, MailAuthError)


def test_attachment_goes_along_with_its_name_and_type(tmp_path):
    pdf = tmp_path / "Rechnung.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    message = build_message(
        CONFIG,
        to_addresses=["a@example.invalid"],
        to_name="A",
        subject="s",
        body="b",
        attachments=[Attachment(pdf, "Rechnung_34.pdf")],
    )
    (attachment,) = list(message.iter_attachments())
    assert attachment.get_filename() == "Rechnung_34.pdf"
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_content() == b"%PDF-1.4 test"


def test_no_address_missing_file_and_oversize_are_per_recipient_errors(tmp_path):
    with pytest.raises(MailError, match="Keine E-Mail-Adresse"):
        build_message(CONFIG, to_addresses=[], to_name="Vreni", subject="s", body="b")
    with pytest.raises(MailError, match="weg.pdf"):
        build_message(
            CONFIG,
            to_addresses=["a@example.invalid"],
            to_name="A",
            subject="s",
            body="b",
            attachments=[Attachment(tmp_path / "weg.pdf", "weg.pdf")],
        )
    big = tmp_path / "gross.pdf"
    big.write_bytes(b"x" * (MAX_INLINE_ATTACHMENT_BYTES + 1))
    with pytest.raises(MailError, match="zu gross"):
        build_message(
            CONFIG,
            to_addresses=["a@example.invalid"],
            to_name="A",
            subject="s",
            body="b",
            attachments=[Attachment(big, "gross.pdf")],
        )


# --- Sending -------------------------------------------------------------------------------------


def test_ssl_send_logs_in_sends_and_says_goodbye():
    _send()
    (server,) = FakeSmtp.instances
    assert (server.host, server.port) == ("mail.example.invalid", 465)
    assert server.context is not None
    assert server.calls == ["login info@sunnesyte.invalid", "send", "quit"]


def test_starttls_encrypts_before_the_password_goes_over_the_wire():
    _send(STARTTLS)
    (server,) = FakeSmtp.instances
    assert server.port == 587
    assert server.calls[:2] == ["starttls", "login info@sunnesyte.invalid"]


def test_no_starttls_on_offer_is_an_error_and_no_password_is_sent():
    FakeSmtp.starttls_error = smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")
    with pytest.raises(MailError, match="STARTTLS"):
        _send(STARTTLS)
    assert FakeSmtp.instances[0].calls == ["starttls", "close"]


def test_wrong_password_aborts_with_an_auth_error():
    """A MailAuthError, so a batch send stops instead of failing once per recipient."""
    FakeSmtp.login_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Error: authentication failed")
    with pytest.raises(MailAuthError, match="535 5.7.8 Error: authentication failed"):
        _send()


def test_refused_sender_aborts_too():
    FakeSmtp.send_error = smtplib.SMTPSenderRefused(
        553, b"5.7.1 Sender address rejected", "info@sunnesyte.invalid"
    )
    with pytest.raises(MailAuthError, match="553"):
        _send()


def test_spam_rejection_is_reported_in_the_servers_own_words():
    """What Infomaniak answered to a bare one-line test mail."""
    FakeSmtp.send_error = smtplib.SMTPDataError(550, b"5.2.0 Spam message rejected")
    with pytest.raises(MailError, match="550 5.2.0 Spam message rejected") as excinfo:
        _send()
    assert not isinstance(excinfo.value, MailAuthError)


def test_refused_recipient_is_named():
    FakeSmtp.send_error = smtplib.SMTPRecipientsRefused(
        {"vreni@example.invalid": (550, b"5.1.1 User unknown")}
    )
    with pytest.raises(MailError, match="vreni@example.invalid: 550 5.1.1 User unknown"):
        _send()


def test_unreachable_server_is_a_per_recipient_error():
    FakeSmtp.connect_error = ConnectionRefusedError(61, "Connection refused")
    with pytest.raises(MailError, match="nicht erreichbar") as excinfo:
        _send()
    assert not isinstance(excinfo.value, MailAuthError)


def test_partly_refused_couple_still_counts_as_sent(caplog):
    FakeSmtp.refused = {"jamie@example.invalid": (550, b"User unknown")}
    with caplog.at_level(logging.WARNING, logger="app.emailing.smtp_client"):
        _send(to_addresses=["yvonne@example.invalid", "jamie@example.invalid"])
    assert "jamie@example.invalid" in caplog.text


def test_batch_keeps_the_configured_pause_between_two_messages():
    paced = SmtpConfig(**{**CONFIG.__dict__, "send_delay_seconds": 2.0})
    with (
        patch.object(smtp_client, "_now", side_effect=[100.0, 100.5, 102.5]),
        patch.object(smtp_client.asyncio, "sleep", AsyncMock()) as sleep,
    ):
        _send(paced)
        _send(paced)
    sleep.assert_awaited_once()
    assert sleep.await_args.args[0] == pytest.approx(1.5)


# --- The copy under "Gesendet" -------------------------------------------------------------------


def test_sent_message_is_filed_read_in_the_folder_flagged_sent():
    _send(subject="Abrechnung Nr. 34")
    (imap,) = FakeImap.instances
    ((mailbox, flags, raw),) = imap.appended
    assert mailbox == '"Sent"'
    assert flags == r"(\Seen)"
    assert message_from_bytes(raw)["Subject"] == "Abrechnung Nr. 34"
    assert imap.calls[-1] == "logout"


def test_configured_folder_is_used_without_asking_the_server():
    _send(SmtpConfig(**{**CONFIG.__dict__, "imap_sent_folder": "INBOX.Gesendet"}))
    (imap,) = FakeImap.instances
    assert imap.appended[0][0] == '"INBOX.Gesendet"'
    assert "list" not in imap.calls


def test_no_copy_wanted_means_no_imap_connection():
    _send(NO_COPY)
    assert FakeImap.instances == []


def test_failed_send_files_no_copy():
    FakeSmtp.send_error = smtplib.SMTPDataError(550, b"rejected")
    with pytest.raises(MailError):
        _send()
    assert FakeImap.instances == []


def test_failed_copy_does_not_turn_a_sent_mail_into_a_failed_one(caplog):
    """The mail is out; reporting it as failed would make the administrator send it twice."""
    FakeImap.login_error = smtp_client.imaplib.IMAP4.error("LOGIN failed")
    with caplog.at_level(logging.WARNING, logger="app.emailing.smtp_client"):
        _send()
    assert FakeSmtp.instances[0].sent
    assert "nicht unter „Gesendet“ abgelegt" in caplog.text


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (LIST_WITH_FLAG, '"Sent"'),
        (
            [b'(\\HasNoChildren) "/" INBOX', b'(\\HasNoChildren) "/" "Gesendete Elemente"'],
            '"Gesendete Elemente"',
        ),
        ([b'(\\HasNoChildren) "." INBOX.Sent'], "INBOX.Sent"),
        ([b'(\\HasNoChildren) "/" "INBOX/Gesendet"'], '"INBOX/Gesendet"'),
        ([b'(\\HasNoChildren) "/" INBOX', (b'(\\HasNoChildren) "/" {4}', b"Sent")], None),
        ([b'(\\HasNoChildren) "/" INBOX', None], None),
    ],
)
def test_find_sent_folder(lines, expected):
    assert find_sent_folder(lines) == expected


def test_quote_mailbox_quotes_once_and_escapes():
    assert quote_mailbox("Sent Items") == '"Sent Items"'
    assert quote_mailbox('"Sent"') == '"Sent"'
    assert quote_mailbox('A"B') == '"A\\"B"'


# --- Checking without sending ------------------------------------------------------------------


def test_check_connection_logs_in_to_both_and_names_the_folder():
    description = asyncio.run(check_connection(CONFIG))
    assert FakeSmtp.instances[0].calls == ["login info@sunnesyte.invalid", "quit"]
    assert FakeImap.instances[0].appended == []
    assert '"Sent"' in description and "info@sunnesyte.invalid" in description


def test_check_connection_without_copy_skips_imap():
    assert "keine Kopie" in asyncio.run(check_connection(NO_COPY))
    assert FakeImap.instances == []


def test_check_connection_says_when_no_sent_folder_can_be_found():
    FakeImap.list_lines = [b'(\\HasNoChildren) "/" "INBOX"']
    with pytest.raises(MailError, match="Ordnernamen"):
        asyncio.run(check_connection(CONFIG))
