"""Tests for `app.emailing.templates.compose_with_signature`, which the Rundmail and the Textbausteine share
(it used to live in `app.gui.pages.email_dispatch`)."""

from app.emailing.templates import compose_with_signature


def test_compose_with_signature_returns_plain_body_when_no_signature_chosen():
    assert compose_with_signature("Guten Tag", "") == "Guten Tag"


def test_compose_with_signature_appends_signature_with_delimiter():
    result = compose_with_signature("Guten Tag", "Freundliche Grüsse\nDer Vorstand")
    assert result.startswith("Guten Tag\n\n-- \n")
    assert result.endswith("Freundliche Grüsse\nDer Vorstand")


def test_compose_with_signature_does_not_mutate_original_message():
    """The signature must never be baked into the composed message text itself -- only into the value..."""
    body = "Guten Tag"
    compose_with_signature(body, "Irgendeine Signatur")
    assert body == "Guten Tag"


def test_compose_with_signature_treats_whitespace_only_signature_as_none():
    assert compose_with_signature("Guten Tag", "   \n  ") == "Guten Tag"
