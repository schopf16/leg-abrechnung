"""Tests for app.emailing.graph_client (mocked httpx.AsyncClient -- no real contact with Microsoft)."""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import GraphConfig
from app.emailing.graph_client import (
    Attachment,
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
    """Build a replacement for `httpx.AsyncClient` whose `.post()` is mocked."""
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
    response = httpx.Response(400, json={"error": "invalid_client", "error_description": "bad secret"})
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
                to_addresses=["anna@example.invalid"],
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
                to_addresses=["anna@example.invalid"],
                to_name="Anna Muster",
                subject="Betreff",
                body="Text",
                attachments=[Attachment(path=pdf_path, filename="rechnung.pdf")],
            )
        )

    attachment = client.post.call_args.kwargs["json"]["message"]["attachments"][0]
    assert attachment["name"] == "rechnung.pdf"
    assert attachment["contentType"] == "application/pdf"
    assert base64.b64decode(attachment["contentBytes"]) == b"%PDF-fake-content"


def test_send_email_guesses_content_type_from_filename(tmp_path):
    """A non-PDF attachment (e.g."""
    image_path = tmp_path / "einladung.png"
    image_path.write_bytes(b"\x89PNG-fake-content")
    response = httpx.Response(202)
    client_class, client = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        asyncio.run(
            send_email(
                _CONFIG,
                "token",
                to_addresses=["a@example.invalid"],
                to_name="A",
                subject="s",
                body="b",
                attachments=[Attachment(path=image_path, filename="einladung.png")],
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
                _CONFIG,
                "token",
                to_addresses=["a@example.invalid"],
                to_name="A",
                subject="s",
                body="b",
                attachments=[Attachment(path=unknown_path, filename="datei.xyz123")],
            )
        )

    attachment = client.post.call_args.kwargs["json"]["message"]["attachments"][0]
    assert attachment["contentType"] == "application/octet-stream"


def test_send_email_raises_api_error_for_oversized_attachment(tmp_path):
    """Enforced client-side, before ever calling Graph -- a too-large attachment must not produce N..."""
    from app.emailing.graph_client import MAX_INLINE_ATTACHMENT_BYTES

    big_path = tmp_path / "gross.bin"
    big_path.write_bytes(b"x" * (MAX_INLINE_ATTACHMENT_BYTES + 1))
    client_class, client = _async_client_mock()
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphApiError, match="zu gross"):
            asyncio.run(
                send_email(
                    _CONFIG,
                    "token",
                    to_addresses=["a@example.invalid"],
                    to_name="A",
                    subject="s",
                    body="b",
                    attachments=[Attachment(path=big_path, filename="gross.bin")],
                )
            )
    client.post.assert_not_called()


def test_send_email_raises_api_error_if_attachment_missing(tmp_path):
    """A PDF deleted/moved after being generated must be a clean GraphApiError (so bulk_send can skip..."""
    missing_path = tmp_path / "does-not-exist.pdf"
    client_class, client = _async_client_mock()
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphApiError, match="does-not-exist.pdf"):
            asyncio.run(
                send_email(
                    _CONFIG,
                    "token",
                    to_addresses=["a@example.invalid"],
                    to_name="A",
                    subject="s",
                    body="b",
                    attachments=[Attachment(path=missing_path, filename="does-not-exist.pdf")],
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
                    _CONFIG,
                    "token",
                    to_addresses=["a@example.invalid"],
                    to_name="A",
                    subject="s",
                    body="b",
                )
            )


def test_send_email_retries_on_429_then_succeeds():
    throttled = httpx.Response(429, headers={"Retry-After": "0"})
    success = httpx.Response(202)
    client_class, client = _async_client_mock(post_side_effect=[throttled, success])
    with (
        patch("app.emailing.graph_client.httpx.AsyncClient", client_class),
        patch("app.emailing.graph_client.asyncio.sleep", AsyncMock()),
    ):
        asyncio.run(
            send_email(
                _CONFIG,
                "token",
                to_addresses=["a@example.invalid"],
                to_name="A",
                subject="s",
                body="b",
            )
        )

    assert client.post.call_count == 2


def test_send_email_gives_up_after_max_retries_of_429():
    throttled = httpx.Response(429, headers={"Retry-After": "0"})
    client_class, client = _async_client_mock(post_result=throttled)
    with (
        patch("app.emailing.graph_client.httpx.AsyncClient", client_class),
        patch("app.emailing.graph_client.asyncio.sleep", AsyncMock()),
    ):
        with pytest.raises(GraphApiError):
            asyncio.run(
                send_email(
                    _CONFIG,
                    "token",
                    to_addresses=["a@example.invalid"],
                    to_name="A",
                    subject="s",
                    body="b",
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
                    _CONFIG,
                    "token",
                    to_addresses=["a@example.invalid"],
                    to_name="A",
                    subject="s",
                    body="b",
                )
            )


def test_send_email_raises_api_error_on_network_failure():
    client_class, _ = _async_client_mock(post_side_effect=httpx.ConnectError("no route"))
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphApiError):
            asyncio.run(
                send_email(
                    _CONFIG,
                    "token",
                    to_addresses=["a@example.invalid"],
                    to_name="A",
                    subject="s",
                    body="b",
                )
            )


# --- One message per contract party ------------------------------------
#
# The privacy rule is narrowed, not abandoned: a couple is one Person with
# two addresses, and both go into the one message addressed to them. What
# must never happen is two *different* parties sharing a message -- that
# would disclose one member's address to another. See app.emailing.


def test_both_addresses_of_one_party_share_a_single_message():
    """A couple gets one send, with both of their addresses on it."""
    response = httpx.Response(202)
    client_class, client = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        asyncio.run(
            send_email(
                _CONFIG,
                "token-xyz",
                to_addresses=["anna@example.invalid", "beat@example.invalid"],
                to_name="Anna Muster und Beat Beispiel",
                subject="Betreff",
                body="Text",
            )
        )

    assert client.post.await_count == 1, "ein Paar ist ein Versand, nicht zwei"
    message = client.post.call_args.kwargs["json"]["message"]
    assert message["toRecipients"] == [
        {"emailAddress": {"address": "anna@example.invalid", "name": "Anna Muster und Beat Beispiel"}},
        {"emailAddress": {"address": "beat@example.invalid", "name": "Anna Muster und Beat Beispiel"}},
    ]


def test_no_cc_or_bcc_is_ever_set():
    """The part of the rule that did not change."""
    response = httpx.Response(202)
    client_class, client = _async_client_mock(post_result=response)
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        asyncio.run(
            send_email(
                _CONFIG,
                "token",
                to_addresses=["anna@example.invalid", "beat@example.invalid"],
                to_name="Paar",
                subject="s",
                body="b",
            )
        )

    message = client.post.call_args.kwargs["json"]["message"]
    assert "ccRecipients" not in message
    assert "bccRecipients" not in message


def test_sending_to_nobody_is_a_clean_per_party_error():
    """A `GraphApiError`, so a batch send skips this party instead of aborting."""
    client_class, client = _async_client_mock(post_result=httpx.Response(202))
    with patch("app.emailing.graph_client.httpx.AsyncClient", client_class):
        with pytest.raises(GraphApiError) as exc_info:
            asyncio.run(
                send_email(
                    _CONFIG,
                    "token",
                    to_addresses=[],
                    to_name="Ohne Adresse",
                    subject="s",
                    body="b",
                )
            )

    assert "Ohne Adresse" in str(exc_info.value)
    assert client.post.await_count == 0, "es darf nichts an Microsoft gehen"
