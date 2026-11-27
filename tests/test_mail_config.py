"""Tests for the email transport in app.config: Graph or SMTP, read and validated."""

import json

import pytest

from app import config as config_module
from app.config import (
    ConfigError,
    GraphConfig,
    SmtpConfig,
    get_mail_config,
    mail_config_from,
)

GRAPH = {
    "graph_tenant_id": "tenant-123",
    "graph_client_id": "client-456",
    "graph_client_secret": "secret-789",
    "graph_sender_address": "leg@example.invalid",
    "graph_sender_name": "LEG Test",
}

SMTP = {
    "mail_transport": "smtp",
    "smtp_host": "mail.example.invalid",
    "smtp_port": 465,
    "smtp_security": "ssl",
    "smtp_username": "info@example.invalid",
    "smtp_password": "geheim",
    "smtp_sender_address": "info@example.invalid",
    "smtp_sender_name": "Sunnesyte",
}


@pytest.fixture
def config_path(monkeypatch, tmp_path):
    """A config.local.json of the test's own, never the real one."""
    path = tmp_path / "config.local.json"
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", path)
    return path


def _write(path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_without_mail_transport_it_is_graph_as_before(config_path):
    """Existing installations have no `mail_transport` key and must keep sending via Graph."""
    _write(config_path, GRAPH)
    assert get_mail_config() == GraphConfig(
        "tenant-123", "client-456", "secret-789", "leg@example.invalid", "LEG Test"
    )


def test_smtp_with_all_values(config_path):
    _write(
        config_path,
        {**SMTP, "imap_host": "imap.example.invalid", "imap_port": 1993, "imap_sent_folder": "Sent"},
    )
    assert get_mail_config() == SmtpConfig(
        host="mail.example.invalid",
        port=465,
        security="ssl",
        username="info@example.invalid",
        password="geheim",
        sender_address="info@example.invalid",
        sender_name="Sunnesyte",
        send_delay_seconds=1.0,
        imap_enabled=True,
        imap_host="imap.example.invalid",
        imap_port=1993,
        imap_sent_folder="Sent",
    )


def test_smtp_defaults_follow_the_encryption(config_path):
    """No port: 465 for SSL, 587 for STARTTLS; IMAP on the SMTP host, port 993, copy on."""
    data = {key: value for key, value in SMTP.items() if key != "smtp_port"}
    _write(config_path, {**data, "smtp_security": "starttls"})
    config = get_mail_config()
    assert (config.port, config.imap_host, config.imap_port, config.imap_enabled) == (
        587,
        "mail.example.invalid",
        993,
        True,
    )
    _write(config_path, data)
    assert get_mail_config().port == 465


@pytest.mark.parametrize("missing", ["smtp_host", "smtp_username", "smtp_password", "smtp_sender_address"])
def test_smtp_needs_its_required_values(config_path, missing):
    _write(config_path, {**SMTP, missing: "  "})
    with pytest.raises(ConfigError, match=missing):
        get_mail_config()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("smtp_security", "none", "ssl"),
        ("smtp_port", "abc", "keine Zahl"),
        ("smtp_port", 70000, "65535"),
        ("imap_port", 0, "65535"),
        ("smtp_sender_address", "kein-at", "keine E-Mail-Adresse"),
        ("smtp_send_delay_seconds", -1, "zwischen 0"),
        ("smtp_send_delay_seconds", "x", "keine Zahl"),
        ("imap_sent_folder", "Gesendete Objekte ä", "ASCII"),
        ("mail_transport", "pigeon", "graph"),
    ],
)
def test_smtp_rejects_bad_values_with_a_reason(config_path, key, value, message):
    """A plain-text connection in particular is not on offer: the password would travel in the clear."""
    _write(config_path, {**SMTP, key: value})
    with pytest.raises(ConfigError, match=message):
        get_mail_config()


def test_imap_copy_can_be_switched_off_also_by_hand(config_path):
    _write(config_path, {**SMTP, "imap_enabled": "false"})
    assert get_mail_config().imap_enabled is False


def test_mail_config_from_checks_an_unsaved_form():
    assert isinstance(mail_config_from(SMTP), SmtpConfig)
    assert isinstance(mail_config_from(GRAPH), GraphConfig)
