"""Tests for app.models.email_log (broadcast/LEG email sent-history)."""

from app.models import email_log as email_log_repo
from app.models import leg as leg_repo
from app.models.leg import Leg


def test_create_and_list_all_round_trip(db):
    log_id = email_log_repo.create(
        db,
        scope="alle",
        leg_id=None,
        subject="Betreff {vorname}",
        body="Text {nachname}",
        recipient_emails=["a@example.invalid", "b@example.invalid"],
    )

    entries = email_log_repo.list_all(db)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.id == log_id
    assert entry.scope == "alle"
    assert entry.leg_id is None
    assert entry.subject == "Betreff {vorname}"
    assert entry.body == "Text {nachname}"
    assert entry.recipient_emails == ["a@example.invalid", "b@example.invalid"]
    assert entry.recipient_count == 2


def test_create_stores_leg_id_for_leg_scope(db):
    leg_id = leg_repo.create(db, Leg(id=None, name="LEG Test", bemerkung="", created_at=""))
    email_log_repo.create(
        db, scope="leg", leg_id=leg_id, subject="s", body="b", recipient_emails=["a@example.invalid"]
    )
    entry = email_log_repo.list_all(db)[0]
    assert entry.scope == "leg"
    assert entry.leg_id == leg_id


def test_list_all_orders_most_recent_first(db):
    email_log_repo.create(db, scope="alle", leg_id=None, subject="erste", body="", recipient_emails=[])
    email_log_repo.create(db, scope="alle", leg_id=None, subject="zweite", body="", recipient_emails=[])

    entries = email_log_repo.list_all(db)
    assert [e.subject for e in entries] == ["zweite", "erste"]


def test_recipient_count_zero_for_empty_list(db):
    email_log_repo.create(db, scope="alle", leg_id=None, subject="s", body="b", recipient_emails=[])
    assert email_log_repo.list_all(db)[0].recipient_count == 0
