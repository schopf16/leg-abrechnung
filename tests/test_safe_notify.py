"""Tests for `app.gui.safe_notify`, the notify() wrapper introduced after a
production crash ("The parent element this slot belongs to has been
deleted.") was reported when confirming a delete on a card-based list page.
"""

import logging

from nicegui import ui

from app.gui.safe_notify import safe_notify


def test_safe_notify_forwards_to_ui_notify(monkeypatch):
    """When ui.notify() succeeds, safe_notify behaves like a plain passthrough."""
    calls = []
    monkeypatch.setattr(ui, "notify", lambda message, **kwargs: calls.append((message, kwargs)))

    safe_notify("Gespeichert.", type="positive")

    assert calls == [("Gespeichert.", {"type": "positive"})]


def test_safe_notify_swallows_deleted_context_error(monkeypatch, caplog):
    """A RuntimeError from a torn-down UI context is logged, not raised."""

    def raise_deleted(message, **kwargs):
        raise RuntimeError("The parent element this slot belongs to has been deleted.")

    monkeypatch.setattr(ui, "notify", raise_deleted)

    with caplog.at_level(logging.WARNING):
        safe_notify("Gelöscht.", type="warning")

    assert any("Gelöscht." in record.message for record in caplog.records)


def test_safe_notify_does_not_swallow_other_exceptions(monkeypatch):
    """Only the element-lifecycle RuntimeError is tolerated -- anything else still surfaces."""

    def raise_other(message, **kwargs):
        raise ValueError("something unrelated")

    monkeypatch.setattr(ui, "notify", raise_other)

    try:
        safe_notify("x")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError to propagate")
