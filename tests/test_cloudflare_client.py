"""Tests for app.importers.cloudflare_client (mocked httpx -- no real network calls)."""

from unittest.mock import patch

import httpx
import pytest

from app.importers.cloudflare_client import (
    CloudflareApiError,
    CloudflareAuthError,
    fetch_new_registrations,
)

_GET_TARGET = "app.importers.cloudflare_client.httpx.get"


def _api_entry(entry_id: int, form_type: str = "registration", **payload_overrides: object) -> dict:
    """Build one raw API response entry with sensible defaults."""
    payload = {
        "firma": "",
        "anrede": "Frau",
        "vorname": "Anna",
        "nachname": "Muster",
        "strasse": "Musterweg",
        "hausnummer": "1",
        "plz": "3063",
        "ort": "Ittigen",
        "email": "anna@example.ch",
        "telefon": "",
        "bkw_kundennummer": "",
        "iban": "",
        "message": "",
        "meters": [{"meter_number": "CH1022201234500000000000000032841", "note": "PV"}],
    }
    payload.update(payload_overrides)
    return {
        "id": entry_id,
        "created_at": "2026-01-01T10:00:00",
        "form_type": form_type,
        "name": "Anna Muster",
        "email": "anna@example.ch",
        "payload": payload,
    }


def test_fetch_new_registrations_parses_successful_response():
    response = httpx.Response(200, json=[_api_entry(1), _api_entry(2)])
    with patch(_GET_TARGET, return_value=response) as mock_get:
        submissions = fetch_new_registrations(0, "token")

    assert len(submissions) == 2
    assert submissions[0].cloudflare_id == 1
    assert submissions[0].vorname == "Anna"
    assert submissions[0].nachname == "Muster"
    assert submissions[0].meters == [("CH1022201234500000000000000032841", "PV")]
    mock_get.assert_called_once()
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer token"
    assert kwargs["params"]["since"] == 0


def test_fetch_new_registrations_parses_multiple_meters():
    entry = _api_entry(
        1,
        meters=[
            {"meter_number": "CH-PV", "note": "PV"},
            {"meter_number": "CH-HAUS", "note": "Wohnhaus"},
        ],
    )
    response = httpx.Response(200, json=[entry])
    with patch(_GET_TARGET, return_value=response):
        submissions = fetch_new_registrations(0, "token")

    assert submissions[0].meters == [("CH-PV", "PV"), ("CH-HAUS", "Wohnhaus")]


def test_fetch_new_registrations_parses_no_meters():
    entry = _api_entry(1, meters=[])
    response = httpx.Response(200, json=[entry])
    with patch(_GET_TARGET, return_value=response):
        submissions = fetch_new_registrations(0, "token")

    assert submissions[0].meters == []


def test_fetch_new_registrations_drops_meters_with_empty_number():
    entry = _api_entry(
        1,
        meters=[
            {"meter_number": "  ", "note": "leer"},
            {"meter_number": "CH-OK", "note": ""},
        ],
    )
    response = httpx.Response(200, json=[entry])
    with patch(_GET_TARGET, return_value=response):
        submissions = fetch_new_registrations(0, "token")

    assert submissions[0].meters == [("CH-OK", "")]


def test_fetch_new_registrations_raises_on_401():
    response = httpx.Response(401, json={"error": "unauthorized"})
    with patch(_GET_TARGET, return_value=response):
        with pytest.raises(CloudflareAuthError):
            fetch_new_registrations(0, "wrong-token")


def test_fetch_new_registrations_raises_on_other_http_error():
    response = httpx.Response(500, text="internal error")
    with patch(_GET_TARGET, return_value=response):
        with pytest.raises(CloudflareApiError):
            fetch_new_registrations(0, "token")


def test_fetch_new_registrations_raises_on_network_error():
    with patch(_GET_TARGET, side_effect=httpx.ConnectError("no route")):
        with pytest.raises(CloudflareApiError):
            fetch_new_registrations(0, "token")


def test_fetch_new_registrations_filters_out_non_registration_form_type():
    response = httpx.Response(
        200, json=[_api_entry(1, form_type="registration"), _api_entry(2, form_type="question")]
    )
    with patch(_GET_TARGET, return_value=response):
        submissions = fetch_new_registrations(0, "token")

    assert len(submissions) == 1
    assert submissions[0].cloudflare_id == 1


def test_fetch_new_registrations_strips_whitespace_from_payload_fields():
    entry = _api_entry(1, vorname="  Anna  ", email="  anna@example.ch  ")
    response = httpx.Response(200, json=[entry])
    with patch(_GET_TARGET, return_value=response):
        submissions = fetch_new_registrations(0, "token")

    assert submissions[0].vorname == "Anna"
    assert submissions[0].email == "anna@example.ch"


def test_fetch_new_registrations_returns_empty_list_for_empty_response():
    response = httpx.Response(200, json=[])
    with patch(_GET_TARGET, return_value=response):
        submissions = fetch_new_registrations(0, "token")

    assert submissions == []
