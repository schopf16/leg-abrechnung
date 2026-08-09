"""Tests for the web_registration model, migration 18, and
app.importers.registration_sync (mocked -- no real network calls)."""

from unittest.mock import patch

from app.db.schema import get_schema_version
from app.importers.cloudflare_client import RegistrationSubmission
from app.importers.registration_sync import sync_registrations
from app.models import settings as settings_repo
from app.models import web_registration as web_registration_repo

_SYNC_TARGET = "app.importers.registration_sync.fetch_new_registrations"


def _submission(
    cloudflare_id: int,
    email: str = "anna@example.ch",
    firma: str = "",
    anrede: str = "Frau",
    vorname: str = "Anna",
    nachname: str = "Muster",
    strasse: str = "Musterweg",
    hausnummer: str = "1",
    plz: str = "3063",
    ort: str = "Ittigen",
    telefon: str = "",
    bkw_kundennummer: str = "",
    iban: str = "",
    message: str = "",
    submitted_at: str = "2026-01-01T10:00:00",
    meters: list[tuple[str, str]] | None = None,
) -> RegistrationSubmission:
    """Build a `RegistrationSubmission` with sensible defaults for tests."""
    return RegistrationSubmission(
        cloudflare_id=cloudflare_id,
        firma=firma,
        anrede=anrede,
        vorname=vorname,
        nachname=nachname,
        strasse=strasse,
        hausnummer=hausnummer,
        plz=plz,
        ort=ort,
        email=email,
        telefon=telefon,
        bkw_kundennummer=bkw_kundennummer,
        iban=iban,
        message=message,
        submitted_at=submitted_at,
        meters=meters or [],
    )


def test_migration_18_creates_web_registration_tables_and_cursor(db):
    """A fresh database (migrated by the `db` fixture) has the new tables/column."""
    assert get_schema_version(db) == 18
    settings = settings_repo.get_settings(db)
    assert settings.web_registration_cursor == 0
    assert web_registration_repo.list_all(db) == []
    # web_registration_meter must exist and be queryable (empty).
    assert db.execute("SELECT COUNT(*) FROM web_registration_meter").fetchone()[0] == 0


def test_mark_reviewed_sets_needs_review_and_reviewed_at(db):
    with patch(_SYNC_TARGET, side_effect=[[_submission(1)], []]):
        sync_registrations(db, "token")
    reg = web_registration_repo.list_all(db)[0]
    assert reg.needs_review is True
    assert reg.reviewed_at is None

    web_registration_repo.mark_reviewed(db, reg.id)

    reviewed = web_registration_repo.get(db, reg.id)
    assert reviewed.needs_review is False
    assert reviewed.reviewed_at is not None


def test_mark_reviewed_is_idempotent(db):
    with patch(_SYNC_TARGET, side_effect=[[_submission(1)], []]):
        sync_registrations(db, "token")
    reg_id = web_registration_repo.list_all(db)[0].id

    web_registration_repo.mark_reviewed(db, reg_id)
    web_registration_repo.mark_reviewed(db, reg_id)

    reviewed = web_registration_repo.get(db, reg_id)
    assert reviewed.needs_review is False
    assert reviewed.reviewed_at is not None


def test_sync_registrations_creates_new_row_needing_review(db):
    with patch(_SYNC_TARGET, side_effect=[[_submission(1, email="new@example.ch")], []]):
        result = sync_registrations(db, "token")

    assert result.neu == 1
    assert result.aktualisiert == 0
    assert result.unveraendert == 0
    reg = web_registration_repo.get_by_email(db, "new@example.ch")
    assert reg is not None
    assert reg.needs_review is True
    assert reg.anzeige_name == "Anna Muster"


def test_sync_registrations_creates_row_with_no_meters(db):
    with patch(_SYNC_TARGET, side_effect=[[_submission(1, email="nometer@example.ch", meters=[])], []]):
        result = sync_registrations(db, "token")

    assert result.neu == 1
    reg = web_registration_repo.get_by_email(db, "nometer@example.ch")
    assert reg.meters == []


def test_sync_registrations_creates_multiple_meter_rows(db):
    meters = [("CH-PV", "PV"), ("CH-HAUS", "Wohnhaus")]
    with patch(_SYNC_TARGET, side_effect=[[_submission(1, email="multi@example.ch", meters=meters)], []]):
        result = sync_registrations(db, "token")

    assert result.neu == 1
    reg = web_registration_repo.get_by_email(db, "multi@example.ch")
    assert [(m.meter_number, m.note) for m in reg.meters] == meters


def test_sync_registrations_unchanged_repeat_keeps_needs_review_false(db):
    meters = [("CH-A", "PV")]
    with patch(_SYNC_TARGET, side_effect=[[_submission(1, email="a@example.ch", meters=meters)], []]):
        sync_registrations(db, "token")
    reg = web_registration_repo.get_by_email(db, "a@example.ch")
    web_registration_repo.mark_reviewed(db, reg.id)
    assert web_registration_repo.get(db, reg.id).needs_review is False

    # Same content, same email, arriving again with a higher cloudflare_id.
    with patch(_SYNC_TARGET, side_effect=[[_submission(2, email="a@example.ch", meters=meters)], []]):
        result = sync_registrations(db, "token")

    assert result.unveraendert == 1
    assert result.aktualisiert == 0
    still_reviewed = web_registration_repo.get_by_email(db, "a@example.ch")
    assert still_reviewed.needs_review is False


def test_sync_registrations_changed_field_reflags_for_review(db):
    with patch(_SYNC_TARGET, side_effect=[[_submission(1, email="b@example.ch", telefon="111")], []]):
        sync_registrations(db, "token")
    reg = web_registration_repo.get_by_email(db, "b@example.ch")
    web_registration_repo.mark_reviewed(db, reg.id)
    assert web_registration_repo.get(db, reg.id).needs_review is False

    with patch(_SYNC_TARGET, side_effect=[[_submission(2, email="b@example.ch", telefon="222")], []]):
        result = sync_registrations(db, "token")

    assert result.aktualisiert == 1
    assert result.unveraendert == 0
    updated = web_registration_repo.get_by_email(db, "b@example.ch")
    assert updated.needs_review is True
    assert updated.reviewed_at is None
    assert updated.telefon == "222"
    # The row is updated in place, not duplicated.
    assert len(web_registration_repo.list_all(db)) == 1


def test_sync_registrations_changed_meter_set_reflags_for_review_and_replaces_rows(db):
    with patch(
        _SYNC_TARGET,
        side_effect=[[_submission(1, email="c@example.ch", meters=[("CH-OLD", "")])], []],
    ):
        sync_registrations(db, "token")
    reg = web_registration_repo.get_by_email(db, "c@example.ch")
    web_registration_repo.mark_reviewed(db, reg.id)
    assert web_registration_repo.get(db, reg.id).needs_review is False

    with patch(
        _SYNC_TARGET,
        side_effect=[[_submission(2, email="c@example.ch", meters=[("CH-NEW1", "PV"), ("CH-NEW2", "")])], []],
    ):
        result = sync_registrations(db, "token")

    assert result.aktualisiert == 1
    updated = web_registration_repo.get_by_email(db, "c@example.ch")
    assert updated.needs_review is True
    assert [(m.meter_number, m.note) for m in updated.meters] == [("CH-NEW1", "PV"), ("CH-NEW2", "")]


def test_sync_registrations_advances_cursor_for_noop_entries_too(db):
    with patch(_SYNC_TARGET, side_effect=[[_submission(1, email="d@example.ch")], []]):
        sync_registrations(db, "token")
    assert settings_repo.get_settings(db).web_registration_cursor == 1

    with patch(_SYNC_TARGET, side_effect=[[_submission(1, email="d@example.ch")], []]):
        result = sync_registrations(db, "token")

    assert result.unveraendert == 1
    assert settings_repo.get_settings(db).web_registration_cursor == 1


def test_sync_registrations_second_run_without_new_data_changes_nothing(db):
    with patch(_SYNC_TARGET, side_effect=[[_submission(1, email="e@example.ch")], []]):
        sync_registrations(db, "token")
    before = web_registration_repo.list_all(db)
    cursor_before = settings_repo.get_settings(db).web_registration_cursor

    with patch(_SYNC_TARGET, return_value=[]) as mock_fetch:
        result = sync_registrations(db, "token")

    mock_fetch.assert_called_once_with(cursor_before, "token")
    assert result.neu == 0
    assert result.aktualisiert == 0
    assert result.unveraendert == 0
    assert web_registration_repo.list_all(db) == before
    assert settings_repo.get_settings(db).web_registration_cursor == cursor_before


def test_sync_registrations_skips_entry_without_email(db):
    batch = [
        _submission(1, email="", vorname="Kein", nachname="Mail"),
        _submission(2, email="f@example.ch", vorname="Mit", nachname="Mail"),
    ]
    with patch(_SYNC_TARGET, side_effect=[batch, []]):
        result = sync_registrations(db, "token")

    assert result.neu == 1
    assert len(result.warnings) == 1
    assert "Kein Mail" in result.warnings[0]
    assert web_registration_repo.get_by_email(db, "f@example.ch") is not None
    # The cursor still advances past the skipped entry.
    assert settings_repo.get_settings(db).web_registration_cursor == 2


def test_sync_registrations_paginates_while_page_is_full(db, monkeypatch):
    """Even without hitting the real 500-row cap, the loop must keep
    calling fetch_new_registrations with an increasing `since` until an
    empty batch is returned."""
    calls = []

    def fake_fetch(since, token):
        calls.append(since)
        if since == 0:
            return [_submission(1, email="g1@example.ch"), _submission(2, email="g2@example.ch")]
        if since == 2:
            return [_submission(3, email="g3@example.ch")]
        return []

    monkeypatch.setattr(_SYNC_TARGET, fake_fetch)
    result = sync_registrations(db, "token")

    assert calls == [0, 2, 3]
    assert result.neu == 3
    assert settings_repo.get_settings(db).web_registration_cursor == 3
