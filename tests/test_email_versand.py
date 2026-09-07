"""Tests for the small pure helpers in app.gui.pages.email_versand (not
the page rendering itself, which is only smoke-tested live -- see the
rest of this app's GUI test conventions)."""

from app.gui.pages.email_versand import _compose_body


def test_compose_body_returns_plain_body_when_no_signature_chosen():
    assert _compose_body("Guten Tag", "") == "Guten Tag"


def test_compose_body_appends_signature_with_delimiter():
    result = _compose_body("Guten Tag", "Freundliche Grüsse\nDer Vorstand")
    assert result.startswith("Guten Tag\n\n-- \n")
    assert result.endswith("Freundliche Grüsse\nDer Vorstand")


def test_compose_body_does_not_mutate_original_message():
    """The signature must never be baked into the composed message text
    itself -- only into the value passed on to preview/send, so switching
    the signature selection never requires retyping the message."""
    body = "Guten Tag"
    _compose_body(body, "Irgendeine Signatur")
    assert body == "Guten Tag"


def test_compose_body_treats_whitespace_only_signature_as_none():
    assert _compose_body("Guten Tag", "   \n  ") == "Guten Tag"
