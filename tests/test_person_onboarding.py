"""Tests for the person_onboarding model and migration 20."""

from datetime import date, timedelta

from app.db.schema import get_schema_version
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models import settings as settings_repo
from app.models.leg import Leg
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


def test_migration_20_creates_person_onboarding_table_and_threshold_column(db):
    """A fresh database (migrated by the `db` fixture) has the new table/column."""
    assert get_schema_version(db) == 43
    assert settings_repo.get_settings(db).onboarding_overdue_days == 30
    assert person_onboarding_repo.list_all(db) == []


def test_start_for_person_creates_tracker_with_step_1_date(db):
    person_id = _person(db)
    started = date.today() - timedelta(days=3)

    onboarding = person_onboarding_repo.start_for_person(db, person_id, registered_at=started)

    assert onboarding.id is not None
    assert onboarding.person_id == person_id
    assert onboarding.registered_at == started
    assert onboarding.leg_assigned_at is None
    assert onboarding.is_complete is False


def test_start_for_person_without_date_leaves_step_1_open(db):
    person_id = _person(db)
    onboarding = person_onboarding_repo.start_for_person(db, person_id)
    assert onboarding.registered_at is None
    assert onboarding.current_step == ("registered_at", "Anmeldung bei uns")


def test_start_for_person_is_idempotent(db):
    person_id = _person(db)
    first = person_onboarding_repo.start_for_person(db, person_id, registered_at=date.today())
    second = person_onboarding_repo.start_for_person(
        db, person_id, registered_at=date.today() - timedelta(days=99)
    )

    assert second.id == first.id
    # The second call must not overwrite the existing tracker's data.
    assert second.registered_at == first.registered_at
    assert person_onboarding_repo.list_all(db) == [first]


def test_get_by_person_returns_none_when_not_started(db):
    person_id = _person(db)
    assert person_onboarding_repo.get_by_person(db, person_id) is None


def test_current_step_since_falls_back_to_created_at_for_first_step(db):
    person_id = _person(db)
    onboarding = person_onboarding_repo.start_for_person(db, person_id)  # registered_at left None
    assert onboarding.current_step_since == date.fromisoformat(onboarding.created_at[:10])


def test_current_step_since_uses_previous_step_date(db):
    person_id = _person(db)
    step1 = date.today() - timedelta(days=15)
    onboarding = person_onboarding_repo.start_for_person(db, person_id, registered_at=step1)
    assert onboarding.current_step_since == step1


def test_days_open_and_is_overdue_across_states(db):
    person_id = _person(db)
    onboarding = person_onboarding_repo.start_for_person(
        db, person_id, registered_at=date.today() - timedelta(days=30)
    )

    assert onboarding.days_open() == 30
    assert onboarding.is_overdue(30) is True   # exactly at the threshold counts as overdue
    assert onboarding.is_overdue(31) is False
    assert onboarding.is_overdue(29) is True


def test_days_open_and_is_overdue_are_none_false_when_complete(db):
    person_id = _person(db)
    onboarding = person_onboarding_repo.start_for_person(
        db, person_id, registered_at=date.today() - timedelta(days=999)
    )
    onboarding.leg_assigned_at = date.today()
    onboarding.contract_signed_at = date.today()
    onboarding.bkw_registered_at = date.today()
    onboarding.bkw_confirmed_at = date.today()
    person_onboarding_repo.update(db, onboarding)

    completed = person_onboarding_repo.get_by_person(db, person_id)
    assert completed.is_complete is True
    assert completed.days_open() is None
    assert completed.is_overdue(0) is False


def test_update_persists_all_steps_and_leg(db):
    person_id = _person(db)
    leg_id = leg_repo.create(db, Leg(id=None, name="LEG Test", note="", created_at=""))
    onboarding = person_onboarding_repo.start_for_person(db, person_id, registered_at=date.today())

    onboarding.leg_assigned_at = date.today()
    onboarding.leg_id = leg_id
    person_onboarding_repo.update(db, onboarding)

    reloaded = person_onboarding_repo.get(db, onboarding.id)
    assert reloaded.leg_id == leg_id
    assert reloaded.leg_assigned_at == date.today()
    assert reloaded.current_step == ("contract_signed_at", "Gesellschaftsvertrag unterzeichnet")


def test_delete_removes_tracker_but_keeps_person(db):
    person_id = _person(db)
    onboarding = person_onboarding_repo.start_for_person(db, person_id)

    person_onboarding_repo.delete(db, onboarding.id)

    assert person_onboarding_repo.get_by_person(db, person_id) is None
    assert person_repo.get(db, person_id) is not None


def test_list_in_progress_excludes_completed_trackers(db):
    in_progress_id = _person(db, "InProgress")
    person_onboarding_repo.start_for_person(db, in_progress_id, registered_at=date.today())

    done_id = _person(db, "Done")
    done = person_onboarding_repo.start_for_person(db, done_id, registered_at=date.today())
    done.leg_assigned_at = date.today()
    done.contract_signed_at = date.today()
    done.bkw_registered_at = date.today()
    done.bkw_confirmed_at = date.today()
    person_onboarding_repo.update(db, done)

    in_progress = person_onboarding_repo.list_in_progress(db)
    assert [o.person_id for o in in_progress] == [in_progress_id]


def test_existing_persons_never_get_an_implicit_tracker(db):
    """A Person created without ever starting onboarding has no tracker --
    the whole point of the separate, opt-in table."""
    person_id = _person(db, "Legacy")
    assert person_onboarding_repo.get_by_person(db, person_id) is None
    assert person_onboarding_repo.list_all(db) == []
