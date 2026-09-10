"""Tests for the person_offboarding model and migration 27."""

from datetime import date, timedelta

from app.db.schema import get_schema_version
from app.models import person as person_repo
from app.models import person_offboarding as person_offboarding_repo
from app.models.person import Person


def _person(db, name: str = "Test") -> int:
    """Create a person and return its id."""
    return person_repo.create(
        db,
        Person(
            id=None, anrede="", firma="", vorname=name, nachname="",
            kontakt_email="", kontakt_telefon="",
            rechnungsadresse_strasse="", rechnungsadresse_hausnummer="", rechnungsadresse_plz="",
            rechnungsadresse_ort="", rechnungsadresse_land="CH",
            iban="", kundennummer=None, bkw_kundennummer=None, papierrechnung=False, aktiv=True, created_at="",
        ),
    )


def test_migration_27_creates_person_offboarding_table(db):
    assert get_schema_version(db) == 40
    assert person_offboarding_repo.list_all(db) == []


def test_start_for_person_creates_tracker_with_grund_and_step_1_date(db):
    person_id = _person(db)
    started = date.today() - timedelta(days=3)

    offboarding = person_offboarding_repo.start_for_person(
        db, person_id, grund="freiwillig", beschlossen_am=started
    )

    assert offboarding.id is not None
    assert offboarding.person_id == person_id
    assert offboarding.grund == "freiwillig"
    assert offboarding.beschlossen_am == started
    assert offboarding.metering_point_exit_at is None
    assert offboarding.is_complete is False


def test_start_for_person_without_date_leaves_step_1_open(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(db, person_id, grund="zahlungsverzug")
    assert offboarding.beschlossen_am is None
    assert offboarding.current_step == ("beschlossen_am", "Austritt/Ausschluss beschlossen")


def test_start_for_person_is_idempotent(db):
    person_id = _person(db)
    first = person_offboarding_repo.start_for_person(db, person_id, grund="freiwillig", beschlossen_am=date.today())
    second = person_offboarding_repo.start_for_person(
        db, person_id, grund="zahlungsverzug", beschlossen_am=date.today() - timedelta(days=99)
    )

    assert second.id == first.id
    # The second call must not overwrite the existing tracker -- neither
    # its date nor (importantly) its original grund.
    assert second.beschlossen_am == first.beschlossen_am
    assert second.grund == "freiwillig"


def test_get_by_person_returns_none_when_not_started(db):
    person_id = _person(db)
    assert person_offboarding_repo.get_by_person(db, person_id) is None


def test_current_step_since_falls_back_to_created_at_for_first_step(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(db, person_id, grund="freiwillig")
    assert offboarding.current_step_since == date.fromisoformat(offboarding.created_at[:10])


def test_days_open_across_states(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(
        db, person_id, grund="freiwillig", beschlossen_am=date.today() - timedelta(days=10)
    )
    assert offboarding.days_open() == 10


def test_update_persists_all_steps(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(
        db, person_id, grund="zahlungsverzug", beschlossen_am=date.today()
    )

    offboarding.metering_point_exit_at = date.today()
    offboarding.bkw_informiert_am = date.today()
    person_offboarding_repo.update(db, offboarding)

    reloaded = person_offboarding_repo.get(db, offboarding.id)
    assert reloaded.metering_point_exit_at == date.today()
    assert reloaded.bkw_informiert_am == date.today()
    assert reloaded.current_step == ("person_bestaetigt_am", "Person schriftlich bestätigt")


def test_is_complete_once_all_four_steps_set(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(
        db, person_id, grund="freiwillig", beschlossen_am=date.today()
    )
    offboarding.metering_point_exit_at = date.today()
    offboarding.bkw_informiert_am = date.today()
    offboarding.person_bestaetigt_am = date.today()
    person_offboarding_repo.update(db, offboarding)

    completed = person_offboarding_repo.get_by_person(db, person_id)
    assert completed.is_complete is True
    assert completed.days_open() is None


def test_delete_removes_tracker_but_keeps_person(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(db, person_id, grund="freiwillig")

    person_offboarding_repo.delete(db, offboarding.id)

    assert person_offboarding_repo.get_by_person(db, person_id) is None
    assert person_repo.get(db, person_id) is not None


def test_list_in_progress_excludes_completed_trackers(db):
    in_progress_id = _person(db, "InProgress")
    person_offboarding_repo.start_for_person(db, in_progress_id, grund="freiwillig", beschlossen_am=date.today())

    done_id = _person(db, "Done")
    done = person_offboarding_repo.start_for_person(db, done_id, grund="freiwillig", beschlossen_am=date.today())
    done.metering_point_exit_at = date.today()
    done.bkw_informiert_am = date.today()
    done.person_bestaetigt_am = date.today()
    person_offboarding_repo.update(db, done)

    in_progress = person_offboarding_repo.list_in_progress(db)
    assert [o.person_id for o in in_progress] == [in_progress_id]


def test_existing_persons_never_get_an_implicit_tracker(db):
    person_id = _person(db, "Legacy")
    assert person_offboarding_repo.get_by_person(db, person_id) is None
    assert person_offboarding_repo.list_all(db) == []
