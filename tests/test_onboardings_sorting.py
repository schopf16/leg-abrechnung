"""Tests for the Aufnahmen page's sort options (app.gui.pages.onboardings).

`sort_onboardings` is a pure function over already-loaded trackers, so it
is testable without rendering any NiceGUI page.
"""

from datetime import date, timedelta

from app.gui.pages.onboardings import DEFAULT_SORT, sort_onboardings, sort_options
from app.models.person import Person
from app.models.person_onboarding import STEPS, PersonOnboarding


def _person(person_id: int, last_name: str = "", first_name: str = "", company: str = "") -> Person:
    """Build an unpersisted `Person` with just the name fields that matter here."""
    return Person(
        id=person_id,
        salutation="",
        company=company,
        first_name=first_name,
        last_name=last_name,
        contact_email="",
        contact_phone="",
        billing_street="",
        billing_house_number="",
        billing_postal_code="",
        billing_city="",
        billing_country="CH",
        iban="",
        customer_number=None,
        bkw_customer_number=None,
        paper_invoice=False,
        active=True,
        created_at="",
    )


def _onboarding(
    onboarding_id: int,
    person_id: int,
    *,
    registered_at: date | None = None,
    created_at: str = "2026-01-01T00:00:00+00:00",
    completed_steps: int = 0,
) -> PersonOnboarding:
    """Build an unpersisted tracker with the first `completed_steps` steps dated."""
    onboarding = PersonOnboarding(
        id=onboarding_id,
        person_id=person_id,
        registered_at=registered_at,
        leg_assigned_at=None,
        leg_id=None,
        contract_signed_at=None,
        bkw_registered_at=None,
        bkw_confirmed_at=None,
        created_at=created_at,
    )
    for attr, _ in STEPS[:completed_steps]:
        setattr(onboarding, attr, date(2026, 1, 1))
    return onboarding


def test_default_is_last_name():
    """The administrator looks people up by name, so that is the default."""
    options = sort_options({})
    assert DEFAULT_SORT == "last_name"
    assert options[0].key == DEFAULT_SORT
    assert options[0].label == "Nachname"


def test_sorts_by_last_name_case_insensitively():
    persons = {
        1: _person(1, last_name="Zimmermann", first_name="Anna"),
        2: _person(2, last_name="anderegg", first_name="Beat"),
        3: _person(3, last_name="Müller", first_name="Carla"),
    }
    onboardings = [_onboarding(i, i) for i in (1, 2, 3)]

    order = [o.person_id for o in sort_onboardings(onboardings, persons, "last_name")]

    assert order == [2, 3, 1]


def test_last_name_falls_back_to_company_and_breaks_ties_on_first_name():
    """A company without a contact person sorts under its company name --
    the same rule `person_repo.list_all` uses."""
    persons = {
        1: _person(1, company="Wyder AG"),
        2: _person(2, last_name="Muster", first_name="Zoe"),
        3: _person(3, last_name="Muster", first_name="Adrian"),
    }
    onboardings = [_onboarding(i, i) for i in (1, 2, 3)]

    order = [o.person_id for o in sort_onboardings(onboardings, persons, "last_name")]

    assert order == [3, 2, 1]


def test_sorts_by_registration_date_oldest_first():
    persons = {i: _person(i, last_name=f"P{i}") for i in (1, 2, 3)}
    onboardings = [
        _onboarding(1, 1, registered_at=date(2026, 5, 1)),
        _onboarding(2, 2, registered_at=date(2026, 1, 15)),
        _onboarding(3, 3, registered_at=date(2026, 3, 9)),
    ]

    order = [o.person_id for o in sort_onboardings(onboardings, persons, "registered_at")]

    assert order == [2, 3, 1]


def test_registration_date_falls_back_to_created_at_when_step_1_is_open():
    """A tracker started without a date must not jump to the very top --
    it is placed on the day tracking began instead."""
    persons = {i: _person(i, last_name=f"P{i}") for i in (1, 2)}
    onboardings = [
        _onboarding(1, 1, registered_at=date(2026, 1, 1)),
        _onboarding(2, 2, registered_at=None, created_at="2026-06-01T09:00:00+00:00"),
    ]

    order = [o.person_id for o in sort_onboardings(onboardings, persons, "registered_at")]

    assert order == [1, 2]


def test_sorts_by_current_step_with_completed_trackers_last():
    persons = {i: _person(i, last_name=f"P{i}") for i in (1, 2, 3)}
    onboardings = [
        _onboarding(1, 1, completed_steps=len(STEPS)),  # done
        _onboarding(2, 2, completed_steps=3),
        _onboarding(3, 3, completed_steps=0),
    ]

    order = [o.person_id for o in sort_onboardings(onboardings, persons, "current_step")]

    assert order == [3, 2, 1]


def test_sorts_by_days_open_longest_first_with_completed_last():
    """The point of this ordering is to surface whoever is stuck longest."""
    persons = {i: _person(i, last_name=f"P{i}") for i in (1, 2, 3)}
    today = date.today()
    onboardings = [
        _onboarding(1, 1, registered_at=today - timedelta(days=5)),
        _onboarding(2, 2, registered_at=today - timedelta(days=90)),
        _onboarding(3, 3, completed_steps=len(STEPS)),
    ]

    order = [o.person_id for o in sort_onboardings(onboardings, persons, "days_open")]

    assert order == [2, 1, 3]


def test_unknown_sort_key_falls_back_to_name_and_input_is_not_mutated():
    persons = {1: _person(1, last_name="Zulu"), 2: _person(2, last_name="Alpha")}
    onboardings = [_onboarding(1, 1), _onboarding(2, 2)]

    order = [o.person_id for o in sort_onboardings(onboardings, persons, "does-not-exist")]

    assert order == [2, 1]
    assert [o.person_id for o in onboardings] == [1, 2]


def test_tracker_whose_person_is_missing_still_sorts_without_crashing():
    """A person deleted out from under a tracker must not break the page."""
    persons = {1: _person(1, last_name="Muster")}
    onboardings = [_onboarding(1, 1), _onboarding(2, 999)]

    order = [o.person_id for o in sort_onboardings(onboardings, persons, "last_name")]

    assert order == [999, 1]


def test_umlauts_sort_as_their_base_letter():
    """German rule (DIN 5007 Variant 1): "Bühler" belongs before "Burri",
    not after "Zimmermann" where plain code-point ordering puts it."""
    persons = {
        1: _person(1, last_name="Burri"),
        2: _person(2, last_name="Bühler"),
        3: _person(3, last_name="Buchser"),
        4: _person(4, last_name="Zimmermann"),
    }
    onboardings = [_onboarding(i, i) for i in (1, 2, 3, 4)]

    order = [persons[o.person_id].last_name for o in sort_onboardings(onboardings, persons, "last_name")]

    assert order == ["Buchser", "Bühler", "Burri", "Zimmermann"]


def test_accented_and_sharp_s_names_fold_for_sorting():
    persons = {
        1: _person(1, last_name="Mueller"),
        2: _person(2, last_name="Müller"),
        3: _person(3, last_name="Strasser"),
        4: _person(4, last_name="Straßer"),
        5: _person(5, last_name="Hervé"),
    }
    onboardings = [_onboarding(i, i) for i in (1, 2, 3, 4, 5)]

    order = [persons[o.person_id].last_name for o in sort_onboardings(onboardings, persons, "last_name")]

    # "Müller" folds to "muller", which sorts after "mueller" -- exactly
    # how a German index treats the two spellings. "Straßer" folds to
    # "strasser" and therefore ties with its identical-sounding twin,
    # where the stable sort keeps the incoming order.
    assert order == ["Hervé", "Mueller", "Müller", "Strasser", "Straßer"]
