"""Tests for app.emailing.graph_client (mocked httpx.AsyncClient -- no
real contact with Microsoft)."""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import GraphConfig
from app.emailing.graph_client import (
    GraphApiError,
    GraphAuthError,
    get_access_token,
    send_email,
)

_CONFIG = GraphConfig(
    tenant_id="tenant-1",
    client_id="client-1",
    client_secret="secret-1",
    sender_address="leg@example.invalid",
    sender_name="LEG Test",
)


def _async_client_mock(*, post_result=None, post_side_effect=None):
    """Build a replacement for `httpx.AsyncClient` whose `.post()` is mocked.

    Returns:
        `(client_class, client)`: `client_class` is what replaces
        `httpx.AsyncClient` itself (patch target); `client` is the mock
        instance so callers can assert on `client.post.call_args`.
    """
    client = MagicMock()
    client.post = AsyncMock(return_value=post_result, side_effect=post_side_effect)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    client_class = MagicMock(return_value=context)
    return client_class, client


def test_get_access_token_returns_token_on_success():
    response = httpx.Response(200, json={"access_token": "abc123", "expires_in": 3599})
    client_class, client = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        token = asyncio.run(get_access_token(_CONFIG))

    assert token == "abc123"
    _, kwargs = client.post.call_args
    assert kwargs["data"]["client_id"] == "client-1"
    assert kwargs["data"]["client_secret"] == "secret-1"
    assert kwargs["data"]["grant_type"] == "client_credentials"
    assert kwargs["data"]["scope"] == "https://graph.microsoft.com/.default"


def test_get_access_token_raises_auth_error_on_bad_credentials():
    response = httpx.Response(
        400, json={"error": "invalid_client", "error_description": "bad secret"}
    )
    client_class, _ = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphAuthError, match="bad secret"):
            asyncio.run(get_access_token(_CONFIG))


def test_get_access_token_raises_api_error_on_network_failure():
    client_class, _ = _async_client_mock(post_side_effect=httpx.ConnectError("no route"))
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphApiError):
            asyncio.run(get_access_token(_CONFIG))


def test_get_access_token_raises_api_error_on_malformed_response():
    response = httpx.Response(200, json={"unexpected": "shape"})
    client_class, _ = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphApiError):
            asyncio.run(get_access_token(_CONFIG))


def test_send_email_succeeds_with_one_recipient_only():
    response = httpx.Response(202)
    client_class, client = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        asyncio.run(
            send_email(
                _CONFIG,
                "token-xyz",
                to_address="anna@example.invalid",
                to_name="Anna Muster",
                subject="Betreff",
                body="Text",
            )
        )

    _, kwargs = client.post.call_args
    message = kwargs["json"]["message"]
    assert message["toRecipients"] == [
        {"emailAddress": {"address": "anna@example.invalid", "name": "Anna Muster"}}
    ]
    assert "attachments" not in message
    assert kwargs["headers"]["Authorization"] == "Bearer token-xyz"


def test_send_email_attaches_pdf_as_base64(tmp_path):
    pdf_path = tmp_path / "rechnung.pdf"
    pdf_path.write_bytes(b"%PDF-fake-content")
    response = httpx.Response(202)
    client_class, client = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        asyncio.run(
            send_email(
                _CONFIG,
                "token-xyz",
                to_address="anna@example.invalid",
                to_name="Anna Muster",
                subject="Betreff",
                body="Text",
                attachment_path=pdf_path,
                attachment_filename="rechnung.pdf",
            )
        )

    attachment = client.post.call_args.kwargs["json"]["message"]["attachments"][0]
    assert attachment["name"] == "rechnung.pdf"
    assert attachment["contentType"] == "application/pdf"
    assert base64.b64decode(attachment["contentBytes"]) == b"%PDF-fake-content"


def test_send_email_guesses_content_type_from_filename(tmp_path):
    """A non-PDF attachment (e.g. a broadcast email's administrator-chosen
    file, see app.emailing.bulk_send.send_broadcast_email) still gets a
    sensible contentType instead of the old hardcoded application/pdf."""
    image_path = tmp_path / "einladung.png"
    image_path.write_bytes(b"\x89PNG-fake-content")
    response = httpx.Response(202)
    client_class, client = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        asyncio.run(
            send_email(
                _CONFIG, "token", to_address="a@example.invalid", to_name="A",
                subject="s", body="b",
                attachment_path=image_path, attachment_filename="einladung.png",
            )
        )

    attachment = client.post.call_args.kwargs["json"]["message"]["attachments"][0]
    assert attachment["contentType"] == "image/png"


def test_send_email_falls_back_to_octet_stream_for_unknown_extension(tmp_path):
    unknown_path = tmp_path / "datei.xyz123"
    unknown_path.write_bytes(b"whatever")
    response = httpx.Response(202)
    client_class, client = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        asyncio.run(
            send_email(
                _CONFIG, "token", to_address="a@example.invalid", to_name="A",
                subject="s", body="b",
                attachment_path=unknown_path, attachment_filename="datei.xyz123",
            )
        )

    attachment = client.post.call_args.kwargs["json"]["message"]["attachments"][0]
    assert attachment["contentType"] == "application/octet-stream"


def test_send_email_raises_api_error_for_oversized_attachment(tmp_path):
    """Enforced client-side, before ever calling Graph -- a too-large
    attachment must not produce N confusing per-recipient failures."""
    from app.emailing.graph_client import MAX_INLINE_ATTACHMENT_BYTES

    big_path = tmp_path / "gross.bin"
    big_path.write_bytes(b"x" * (MAX_INLINE_ATTACHMENT_BYTES + 1))
    client_class, client = _async_client_mock()
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphApiError, match="zu gross"):
            asyncio.run(
                send_email(
                    _CONFIG, "token", to_address="a@example.invalid", to_name="A",
                    subject="s", body="b",
                    attachment_path=big_path, attachment_filename="gross.bin",
                )
            )
    client.post.assert_not_called()


def test_send_email_raises_api_error_if_attachment_missing(tmp_path):
    """A PDF deleted/moved after being generated must be a clean
    GraphApiError (so bulk_send can skip just that one recipient), not an
    unhandled OSError that crashes the whole batch."""
    missing_path = tmp_path / "does-not-exist.pdf"
    client_class, client = _async_client_mock()
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphApiError, match="does-not-exist.pdf"):
            asyncio.run(
                send_email(
                    _CONFIG, "token", to_address="a@example.invalid", to_name="A",
                    subject="s", body="b",
                    attachment_path=missing_path, attachment_filename="does-not-exist.pdf",
                )
            )
    client.post.assert_not_called()


def test_send_email_raises_auth_error_on_401():
    response = httpx.Response(401, text="unauthorized")
    client_class, _ = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphAuthError):
            asyncio.run(
                send_email(
                    _CONFIG, "token", to_address="a@example.invalid", to_name="A",
                    subject="s", body="b",
                )
            )


def test_send_email_retries_on_429_then_succeeds():
    throttled = httpx.Response(429, headers={"Retry-After": "0"})
    success = httpx.Response(202)
    client_class, client = _async_client_mock(post_side_effect=[throttled, success])
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class), patch(
        "app.emailing.graph_client.asyncio.sleep", AsyncMock()
    ):
        asyncio.run(
            send_email(
                _CONFIG, "token", to_address="a@example.invalid", to_name="A",
                subject="s", body="b",
            )
        )

    assert client.post.call_count == 2


def test_send_email_gives_up_after_max_retries_of_429():
    throttled = httpx.Response(429, headers={"Retry-After": "0"})
    client_class, client = _async_client_mock(post_result=throttled)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class), patch(
        "app.emailing.graph_client.asyncio.sleep", AsyncMock()
    ):
        with pytest.raises(GraphApiError):
            asyncio.run(
                send_email(
                    _CONFIG, "token", to_address="a@example.invalid", to_name="A",
                    subject="s", body="b",
                )
            )
    assert client.post.call_count > 1


def test_send_email_raises_api_error_on_other_status():
    response = httpx.Response(500, text="server error")
    client_class, _ = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphApiError):
            asyncio.run(
                send_email(
                    _CONFIG, "token", to_address="a@example.invalid", to_name="A",
                    subject="s", body="b",
                )
            )


def test_send_email_raises_api_error_on_network_failure():
    client_class, _ = _async_client_mock(post_side_effect=httpx.ConnectError("no route"))
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphApiError):
            asyncio.run(
                send_email(
                    _CONFIG, "token", to_address="a@example.invalid", to_name="A",
                    subject="s", body="b",
                )
            )
