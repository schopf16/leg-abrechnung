"""Wohneinheiten per Standort, and the potential they add up to per Trafokreis.

The figure is typed in while walking the neighbourhood and cannot be derived:
a metering point exists only once somebody has signed up, so the meters
answer who is already in and never how many there could be.
"""

from datetime import date, timedelta

from app.domain.statistics import substation_area_potential
from app.models import assignment as assignment_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.assignment import Assignment
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.person import Person
from app.models.site import Site
from app.models.substation_area import SubstationArea

_TODAY = date.today()


def _area(db, name: str) -> int:
    """Create a Trafokreis."""
    return substation_area_repo.create(
        db, SubstationArea(id=None, name=name, bkw_designation="", note="", created_at="")
    )


def _site(db, area_id: int, street: str, dwellings: int | None) -> int:
    """Create a Standort in one Trafokreis, with or without a count."""
    return site_repo.create(
        db,
        Site(
            id=None,
            street=street,
            house_number="1",
            postal_code="3063",
            municipality="Beispielhausen",
            address_detail="",
            substation_area_id=area_id,
            created_at="",
            dwelling_count=dwellings,
        ),
    )


def _meter(db, site_id: int, direction: str, designation: str) -> int:
    """Create a metering point at one Standort."""
    leg_id = leg_repo.create(db, Leg(id=None, name=f"LEG {designation}", note="", created_at=""))
    return metering_point_repo.create(
        db,
        MeteringPoint(
            id=None,
            designation=designation,
            direction=direction,
            site_id=site_id,
            leg_id=leg_id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
    )


def _person(db, last_name: str) -> int:
    """Create a person."""
    return person_repo.create(
        db,
        Person(
            id=None,
            salutation="",
            company="",
            first_name="Anna",
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
        ),
    )


def _assign(db, person_id: int, point_id: int, *, until: date | None = None) -> None:
    """Assign a metering point to a person, running today unless ended."""
    assignment_repo.create(
        db,
        Assignment(
            id=None,
            person_id=person_id,
            metering_point_id=point_id,
            valid_from=_TODAY - timedelta(days=30),
            valid_to=until,
            created_at="",
        ),
    )


def _by_name(db) -> dict:
    """The potentials keyed by Trafokreis name."""
    return {potential.name: potential for potential in substation_area_potential(db)}


# --- The count itself ------------------------------------------------------


def test_a_standort_remembers_its_wohneinheiten(db):
    """One for a Einfamilienhaus, several for a Mehrfamilienhaus."""
    area_id = _area(db, "TRA1")
    site_id = _site(db, area_id, "Einzelweg", 1)

    assert site_repo.get(db, site_id).dwelling_count == 1


def test_an_uncounted_standort_stays_none(db):
    """ "Not surveyed" and "nobody lives here" are different statements."""
    area_id = _area(db, "TRA1")
    site_id = _site(db, area_id, "Unbekanntweg", None)

    assert site_repo.get(db, site_id).dwelling_count is None


def test_the_count_survives_an_edit_of_another_field(db):
    """Saving a Standort must not drop the figure."""
    area_id = _area(db, "TRA1")
    site_id = _site(db, area_id, "Hausweg", 8)
    stored = site_repo.get(db, site_id)
    stored.address_detail = "Hinterhaus"
    site_repo.update(db, stored)

    assert site_repo.get(db, site_id).dwelling_count == 8


# --- Adding up per Trafokreis ----------------------------------------------


def test_the_wohneinheiten_add_up_per_trafokreis(db):
    """The whole point: the potential of a neighbourhood."""
    area_id = _area(db, "TRA1")
    _site(db, area_id, "Erstweg", 1)
    _site(db, area_id, "Zweitweg", 12)

    potential = _by_name(db)["TRA1"]

    assert potential.sites == 2
    assert potential.dwellings == 13
    assert potential.sites_open == 0


def test_an_uncounted_standort_is_named_not_counted_as_zero(db):
    """Otherwise the potential looks smaller than it is, and silently."""
    area_id = _area(db, "TRA1")
    _site(db, area_id, "Erstweg", 4)
    _site(db, area_id, "Zweitweg", None)

    potential = _by_name(db)["TRA1"]

    assert potential.dwellings == 4
    assert potential.sites_open == 1, "die ungezählte Adresse wird benannt"


def test_a_trafokreis_with_nothing_counted_has_no_open_figure(db):
    """`None`, not 0 -- that is "unknown", not "nothing left to win"."""
    area_id = _area(db, "TRA1")
    _site(db, area_id, "Erstweg", None)

    potential = _by_name(db)["TRA1"]

    assert not potential.is_counted
    assert potential.open_dwellings is None
    assert potential.participating_share is None


# --- Who already counts as participating -----------------------------------


def test_a_participant_counts_once_however_many_meters(db):
    """One contract party is one household, which is the vZEV model."""
    area_id = _area(db, "TRA1")
    site_id = _site(db, area_id, "Erstweg", 10)
    person_id = _person(db, "Muster")
    _assign(db, person_id, _meter(db, site_id, DIRECTION_CONSUMPTION, "CH1"))
    _assign(db, person_id, _meter(db, site_id, DIRECTION_CONSUMPTION, "CH2"))

    potential = _by_name(db)["TRA1"]

    assert potential.participating == 1
    assert potential.open_dwellings == 9


def test_a_feed_in_meter_alone_is_not_a_participating_household(db):
    """Everyone who feeds in also draws, so the consumption side is the count."""
    area_id = _area(db, "TRA1")
    site_id = _site(db, area_id, "Erstweg", 10)
    _assign(db, _person(db, "Muster"), _meter(db, site_id, DIRECTION_FEED_IN, "CH9"))

    assert _by_name(db)["TRA1"].participating == 0


def test_an_ended_assignment_does_not_count(db):
    """Somebody who moved out is potential again, not a participant."""
    area_id = _area(db, "TRA1")
    site_id = _site(db, area_id, "Erstweg", 10)
    _assign(
        db,
        _person(db, "Muster"),
        _meter(db, site_id, DIRECTION_CONSUMPTION, "CH1"),
        until=_TODAY - timedelta(days=1),
    )

    assert _by_name(db)["TRA1"].participating == 0


def test_somebody_who_starts_next_year_already_counts(db):
    """The case the real data exposed, and it is the whole deployment.

    All 123 assignments in the live database start in the future, because
    the LEG has not begun operating. Judged on today, every participant
    would have been counted as untouched potential -- and the view would
    have sent the administrator to doors where somebody had already signed.
    """
    area_id = _area(db, "TRA1")
    site_id = _site(db, area_id, "Erstweg", 10)
    person_id = _person(db, "Muster")
    assignment_repo.create(
        db,
        Assignment(
            id=None,
            person_id=person_id,
            metering_point_id=_meter(db, site_id, DIRECTION_CONSUMPTION, "CH1"),
            valid_from=_TODAY + timedelta(days=90),
            valid_to=None,
            created_at="",
        ),
    )

    potential = _by_name(db)["TRA1"]

    assert potential.participating == 1
    assert potential.open_dwellings == 9


def test_more_participants_than_counted_units_does_not_go_negative(db):
    """A miscounted address is not a statement about negative potential."""
    area_id = _area(db, "TRA1")
    site_id = _site(db, area_id, "Erstweg", 1)
    _assign(db, _person(db, "Eins"), _meter(db, site_id, DIRECTION_CONSUMPTION, "CH1"))
    _assign(db, _person(db, "Zwei"), _meter(db, site_id, DIRECTION_CONSUMPTION, "CH2"))

    assert _by_name(db)["TRA1"].open_dwellings == 0


# --- The ordering is the verdict -------------------------------------------


def test_the_trafokreis_with_the_most_left_to_win_leads(db):
    """No threshold and no colour: the order is what says where to go."""
    small = _area(db, "TRA klein")
    _site(db, small, "Kleinweg", 2)
    large = _area(db, "TRA gross")
    _site(db, large, "Grossweg", 40)

    assert [p.name for p in substation_area_potential(db)][:2] == ["TRA gross", "TRA klein"]


def test_an_uncounted_trafokreis_comes_last_but_is_named(db):
    """One nobody surveyed otherwise looks exactly like one with no potential."""
    _site(db, _area(db, "TRA gezaehlt"), "Erstweg", 5)
    _area(db, "TRA offen")

    names = [p.name for p in substation_area_potential(db)]

    assert names[-1] == "TRA offen"
    assert "TRA offen" in names


def test_the_view_grades_nothing(db):
    """Facts, like every other statistics view in this app."""
    area_id = _area(db, "TRA1")
    _site(db, area_id, "Erstweg", 20)

    potential = _by_name(db)["TRA1"]
    rendered = " ".join(str(getattr(potential, name)) for name in vars(potential))

    for verdict in ("gut", "schlecht", "kritisch", "ideal", "empfohlen", "Empfehlung"):
        assert verdict.lower() not in rendered.lower(), verdict
