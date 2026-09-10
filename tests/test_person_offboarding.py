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
            id=None, salutation="", company="", first_name=name, last_name="",
            contact_email="", contact_phone="",
            billing_street="", billing_house_number="", billing_postal_code="",
            billing_city="", billing_country="CH",
            iban="", customer_number=None, bkw_customer_number=None, paper_invoice=False, active=True, created_at="",
        ),
    )


def test_migration_27_creates_person_offboarding_table(db):
    assert get_schema_version(db) == 42
    assert person_offboarding_repo.list_all(db) == []


def test_start_for_person_creates_tracker_with_grund_and_step_1_date(db):
    person_id = _person(db)
    started = date.today() - timedelta(days=3)

    offboarding = person_offboarding_repo.start_for_person(
        db, person_id, reason="freiwillig", decided_at=started
    )

    assert offboarding.id is not None
    assert offboarding.person_id == person_id
    assert offboarding.reason == "freiwillig"
    assert offboarding.decided_at == started
    assert offboarding.metering_point_exit_at is None
    assert offboarding.is_complete is False


def test_start_for_person_without_date_leaves_step_1_open(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(db, person_id, reason="zahlungsverzug")
    assert offboarding.decided_at is None
    assert offboarding.current_step == ("decided_at", "Austritt/Ausschluss beschlossen")


def test_start_for_person_is_idempotent(db):
    person_id = _person(db)
    first = person_offboarding_repo.start_for_person(db, person_id, reason="freiwillig", decided_at=date.today())
    second = person_offboarding_repo.start_for_person(
        db, person_id, reason="zahlungsverzug", decided_at=date.today() - timedelta(days=99)
    )

    assert second.id == first.id
    # The second call must not overwrite the existing tracker -- neither
    # its date nor (importantly) its original reason.
    assert second.decided_at == first.decided_at
    assert second.reason == "freiwillig"


def test_get_by_person_returns_none_when_not_started(db):
    person_id = _person(db)
    assert person_offboarding_repo.get_by_person(db, person_id) is None


def test_current_step_since_falls_back_to_created_at_for_first_step(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(db, person_id, reason="freiwillig")
    assert offboarding.current_step_since == date.fromisoformat(offboarding.created_at[:10])


def test_days_open_across_states(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(
        db, person_id, reason="freiwillig", decided_at=date.today() - timedelta(days=10)
    )
    assert offboarding.days_open() == 10


def test_update_persists_all_steps(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(
        db, person_id, reason="zahlungsverzug", decided_at=date.today()
    )

    offboarding.metering_point_exit_at = date.today()
    offboarding.bkw_informed_at = date.today()
    person_offboarding_repo.update(db, offboarding)

    reloaded = person_offboarding_repo.get(db, offboarding.id)
    assert reloaded.metering_point_exit_at == date.today()
    assert reloaded.bkw_informed_at == date.today()
    assert reloaded.current_step == ("person_confirmed_at", "Person schriftlich bestätigt")


def test_is_complete_once_all_four_steps_set(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(
        db, person_id, reason="freiwillig", decided_at=date.today()
    )
    offboarding.metering_point_exit_at = date.today()
    offboarding.bkw_informed_at = date.today()
    offboarding.person_confirmed_at = date.today()
    person_offboarding_repo.update(db, offboarding)

    completed = person_offboarding_repo.get_by_person(db, person_id)
    assert completed.is_complete is True
    assert completed.days_open() is None


def test_delete_removes_tracker_but_keeps_person(db):
    person_id = _person(db)
    offboarding = person_offboarding_repo.start_for_person(db, person_id, reason="freiwillig")

    person_offboarding_repo.delete(db, offboarding.id)

    assert person_offboarding_repo.get_by_person(db, person_id) is None
    assert person_repo.get(db, person_id) is not None


def test_list_in_progress_excludes_completed_trackers(db):
    in_progress_id = _person(db, "InProgress")
    person_offboarding_repo.start_for_person(db, in_progress_id, reason="freiwillig", decided_at=date.today())

    done_id = _person(db, "Done")
    done = person_offboarding_repo.start_for_person(db, done_id, reason="freiwillig", decided_at=date.today())
    done.metering_point_exit_at = date.today()
    done.bkw_informed_at = date.today()
    done.person_confirmed_at = date.today()
    person_offboarding_repo.update(db, done)

    in_progress = person_offboarding_repo.list_in_progress(db)
    assert [o.person_id for o in in_progress] == [in_progress_id]


def test_existing_persons_never_get_an_implicit_tracker(db):
    person_id = _person(db, "Legacy")
    assert person_offboarding_repo.get_by_person(db, person_id) is None
    assert person_offboarding_repo.list_all(db) == []
