"""The E-Mail-Versand page, driven through its own fields and buttons; no mail is sent."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from nicegui import Client, background_tasks, ui

from app import config as config_module
from app.config import GraphConfig, SmtpConfig
from app.emailing import mail
from app.gui.navigation import NAV_GROUPS

GRAPH = {
    "graph_tenant_id": "tenant-123",
    "graph_client_id": "client-456",
    "graph_client_secret": "secret-789",
    "graph_sender_address": "leg@example.invalid",
    "graph_sender_name": "LEG Test",
}


@pytest.fixture(autouse=True)
def config_path(monkeypatch, tmp_path):
    """The page reads and writes a file of the test's own, never the real config.local.json."""
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps({"leg_api_token": "token-abc", **GRAPH}), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_LOCAL_PATH", path)
    return path


def _render(probe: str) -> Client:
    from app.gui.pages.email_settings import email_settings_page

    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        email_settings_page()
    return client


def _label(element) -> str:
    return getattr(element, "label", None) or element._props.get("label", "") or ""


def _elements(client: Client, kind: str) -> list:
    return [element for element in client.elements.values() if element.__class__.__name__ == kind]


def _field(client: Client, label: str, *, card: str = ""):
    """An input or number by its label; `card` picks between the Graph and the SMTP one."""
    matches = [
        element
        for kind in ("Input", "Number", "Select")
        for element in _elements(client, kind)
        if _label(element) == label
    ]
    if card:
        matches = [m for m in matches if card in _ancestor_texts(client, m)]
    (element,) = matches
    return element


def _ancestor_texts(client: Client, element) -> str:
    """The texts of the card an element sits in."""
    parent = element.parent_slot.parent
    while parent is not None and parent.__class__.__name__ != "Card":
        parent = parent.parent_slot.parent if parent.parent_slot else None
    if parent is None:
        return ""
    return " ".join(getattr(child, "text", "") for child in parent.descendants())


def _click(client: Client, text: str) -> None:
    """Click a button; an async handler, which NiceGUI would schedule in the background, is run to its end."""
    (button,) = [b for b in _elements(client, "Button") if b.text == text]
    handler = next(
        listener.handler for listener in button._event_listeners.values() if listener.type == "click"
    )
    scheduled: list = []
    with patch.object(
        background_tasks, "create_or_defer", lambda awaitable, **_: scheduled.append(awaitable)
    ):
        handler(None)
    for awaitable in scheduled:
        asyncio.run(awaitable)


def _feedback(client: Client) -> str:
    """The one line under the buttons."""
    (label,) = [label for label in _elements(client, "Label") if "email-settings-result" in label._markers]
    return label.text


def _fill_smtp(client: Client) -> None:
    (toggle,) = _elements(client, "Toggle")
    toggle.value = "smtp"
    _field(client, "Server").value = "mail.example.invalid"
    _field(client, "Benutzername").value = "info@example.invalid"
    _field(client, "Passwort").value = "geheim"
    _field(client, "Absender-Adresse", card="SMTP").value = "info@example.invalid"
    _field(client, "Absender-Name", card="SMTP").value = "Sunnesyte"


def test_the_page_is_under_einstellungen():
    (settings_group,) = [entries for name, entries in NAV_GROUPS if name == "Einstellungen"]
    assert ("/email-settings", "E-Mail-Versand") in settings_group


def test_the_page_shows_what_is_stored():
    client = _render("/probe-email-settings-shows")
    with client:
        (toggle,) = _elements(client, "Toggle")
        assert toggle.value == "graph"
        assert _field(client, "Tenant-ID").value == "tenant-123"
        assert _field(client, "Port").value == 465


def test_saving_smtp_writes_it_and_keeps_the_rest(config_path):
    client = _render("/probe-email-settings-save")
    with client:
        _fill_smtp(client)
        _click(client, "Speichern")
        assert _feedback(client) == "Gespeichert."
    stored = json.loads(config_path.read_text(encoding="utf-8"))
    assert stored["mail_transport"] == "smtp"
    assert stored["smtp_port"] == 465
    assert stored["leg_api_token"] == "token-abc"
    assert stored["graph_client_secret"] == "secret-789"
    assert isinstance(config_module.get_mail_config(), SmtpConfig)


def test_starttls_moves_the_default_port_but_not_a_typed_one():
    client = _render("/probe-email-settings-port")
    with client:
        security = _field(client, "Verschlüsselung")
        port = _field(client, "Port")
        security.value = "starttls"
        assert port.value == 587
        port.value = 2525
        security.value = "ssl"
        assert port.value == 2525


def test_an_incomplete_form_is_not_saved_and_says_why(config_path):
    before = config_path.read_text(encoding="utf-8")
    client = _render("/probe-email-settings-incomplete")
    with client:
        _fill_smtp(client)
        _field(client, "Passwort").value = ""
        _click(client, "Speichern")
        assert _feedback(client).startswith("Nicht gespeichert") and "smtp_password" in _feedback(client)
    assert config_path.read_text(encoding="utf-8") == before


def test_connection_test_uses_the_form_before_it_is_saved(config_path):
    client = _render("/probe-email-settings-check")
    with client:
        _fill_smtp(client)
        with patch.object(
            mail, "check_connection", AsyncMock(return_value="SMTP mail.example.invalid ok")
        ) as check:
            _click(client, "Verbindung testen")
        assert isinstance(check.await_args.args[0], SmtpConfig)
        assert _feedback(client) == "Verbindung erfolgreich -- SMTP mail.example.invalid ok"
    assert json.loads(config_path.read_text(encoding="utf-8")).get("mail_transport") is None


def test_connection_test_shows_the_servers_refusal():
    client = _render("/probe-email-settings-check-fails")
    with client:
        with patch.object(
            mail, "check_connection", AsyncMock(side_effect=mail.MailAuthError("Anmeldung fehlgeschlagen"))
        ):
            _click(client, "Verbindung testen")
        assert _feedback(client) == "Anmeldung fehlgeschlagen"


def test_test_mail_goes_to_the_sender_and_reads_like_a_real_one():
    """A bare one-liner is what Infomaniak's spam filter rejected; the test mail has greeting and signature."""
    client = _render("/probe-email-settings-test-mail")
    with client:
        with (
            patch.object(mail, "prepare", AsyncMock(return_value="tok")),
            patch.object(mail, "send_email", AsyncMock()) as send,
        ):
            _click(client, "Test-E-Mail senden")
        config, token = send.await_args.args
        assert isinstance(config, GraphConfig) and token == "tok"
        assert send.await_args.kwargs["to_addresses"] == ["leg@example.invalid"]
        body = send.await_args.kwargs["body"]
        assert body.startswith("Guten Tag") and "Freundliche Grüsse\nLEG Test" in body
        assert _feedback(client).startswith("Test-E-Mail an leg@example.invalid gesendet")


def test_test_mail_to_another_address():
    client = _render("/probe-email-settings-test-mail-to")
    with client:
        _field(client, "Test-E-Mail an").value = " jemand@example.invalid "
        with (
            patch.object(mail, "prepare", AsyncMock(return_value="tok")),
            patch.object(mail, "send_email", AsyncMock()) as send,
        ):
            _click(client, "Test-E-Mail senden")
        assert send.await_args.kwargs["to_addresses"] == ["jemand@example.invalid"]


def test_test_mail_to_a_broken_address_is_not_sent():
    client = _render("/probe-email-settings-test-mail-broken")
    with client:
        _field(client, "Test-E-Mail an").value = "jemand@example"
        with patch.object(mail, "send_email", AsyncMock()) as send:
            _click(client, "Test-E-Mail senden")
        send.assert_not_awaited()
        assert _feedback(client).startswith("Test-E-Mail nicht gesendet: E-Mail-Adresse")
