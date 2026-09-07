"""Tests for app.models.signature (named, reusable email signatures)."""

import sqlite3

import pytest

from app.models import signature as signature_repo
from app.models.signature import Signature


def test_create_and_list_all_round_trip(db):
    signature_id = signature_repo.create(
        db, Signature(id=None, name="Vorstand", content="Freundliche Grüsse\nDer Vorstand", created_at="")
    )

    signatures = signature_repo.list_all(db)
    assert len(signatures) == 1
    signature = signatures[0]
    assert signature.id == signature_id
    assert signature.name == "Vorstand"
    assert signature.content == "Freundliche Grüsse\nDer Vorstand"
    assert signature.created_at


def test_list_all_orders_by_name(db):
    signature_repo.create(db, Signature(id=None, name="Zweite", content="B", created_at=""))
    signature_repo.create(db, Signature(id=None, name="Erste", content="A", created_at=""))

    names = [s.name for s in signature_repo.list_all(db)]
    assert names == ["Erste", "Zweite"]


def test_get_returns_none_for_unknown_id(db):
    assert signature_repo.get(db, 999) is None


def test_get_by_name_round_trip(db):
    signature_repo.create(db, Signature(id=None, name="Kassier", content="X", created_at=""))
    found = signature_repo.get_by_name(db, "Kassier")
    assert found is not None
    assert found.name == "Kassier"
    assert signature_repo.get_by_name(db, "Unbekannt") is None


def test_create_rejects_duplicate_name(db):
    signature_repo.create(db, Signature(id=None, name="Vorstand", content="A", created_at=""))
    with pytest.raises(sqlite3.IntegrityError):
        signature_repo.create(db, Signature(id=None, name="Vorstand", content="B", created_at=""))


def test_update_persists_new_name_and_content(db):
    signature_id = signature_repo.create(
        db, Signature(id=None, name="Alt", content="Alter Text", created_at="")
    )
    updated = signature_repo.get(db, signature_id)
    updated.name = "Neu"
    updated.content = "Neuer Text"

    signature_repo.update(db, updated)

    reloaded = signature_repo.get(db, signature_id)
    assert reloaded.name == "Neu"
    assert reloaded.content == "Neuer Text"


def test_update_without_id_raises():
    with pytest.raises(ValueError):
        signature_repo.update(None, Signature(id=None, name="X", content="Y", created_at=""))


def test_update_rejects_duplicate_name_of_a_different_signature(db):
    signature_repo.create(db, Signature(id=None, name="Eins", content="A", created_at=""))
    two_id = signature_repo.create(db, Signature(id=None, name="Zwei", content="B", created_at=""))

    two = signature_repo.get(db, two_id)
    two.name = "Eins"
    with pytest.raises(sqlite3.IntegrityError):
        signature_repo.update(db, two)


def test_delete_removes_signature(db):
    signature_id = signature_repo.create(
        db, Signature(id=None, name="Weg", content="X", created_at="")
    )

    signature_repo.delete(db, signature_id)

    assert signature_repo.get(db, signature_id) is None
    assert signature_repo.list_all(db) == []
