"""Tests for app.domain.participant_mix (Prosumer:Consumer ratio,
one-sided substation areas, LEG upgrade candidates)."""

import itertools
from datetime import date

from app.domain import participant_mix
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models import assignment as assignment_repo
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.person import Person
from app.models.site import Site
from app.models.substation_area import SubstationArea
from app.models.assignment import Assignment

_designation_counter = itertools.count(1)


def _person(db, name: str = "Test") -> int:
    return person_repo.create(
        db,
        Person(
            id=None,
            salutation="",
            company="",
            first_name=name,
            last_name="",
            contact_email=f"{name.lower()}@example.invalid",
            contact_phone="",
            billing_street="Weg",
            billing_house_number="1",
            billing_postal_code="3000",
            billing_city="Bern",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
    )


def _substation_area(db, name: str) -> int:
    return substation_area_repo.create(
        db, SubstationArea(id=None, name=name, bkw_designation="", note="", created_at="")
    )


def _leg(db, name: str) -> int:
    return leg_repo.create(db, Leg(id=None, name=name, note="", created_at=""))


def _site(db, substation_area_id: int, *, street: str = "Weg") -> int:
    return site_repo.create(
        db,
        Site(
            id=None,
            street=street,
            house_number="1",
            postal_code="3000",
            municipality="Bern",
            address_detail="",
            substation_area_id=substation_area_id,
            created_at="",
        ),
    )


def _metering_point(db, site_id: int, leg_id: int | None, direction: str) -> int:
    return metering_point_repo.create(
        db,
        MeteringPoint(
            id=None,
            designation=f"CH{next(_designation_counter):031d}",
            direction=direction,
            site_id=site_id,
            leg_id=leg_id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
    )


def _assignment(
    db, person_id: int, metering_point_id: int, valid_from: date, valid_to: date | None = None
) -> int:
    return assignment_repo.create(
        db,
        Assignment(
            id=None,
            person_id=person_id,
            metering_point_id=metering_point_id,
            valid_from=valid_from,
            valid_to=valid_to,
            created_at="",
        ),
    )


def test_consumer_counted_for_consumption_person(db):
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, None, DIRECTION_CONSUMPTION)
    _assignment(db, person_id, consumption_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix(db, [site_id])

    assert mix.consumer_count == 1
    assert mix.prosumer_count == 0
    assert mix.is_one_sided is True


def test_prosumer_counted_for_feed_in_person(db):
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    person_id = _person(db)
    feed_in_id = _metering_point(db, site_id, None, DIRECTION_FEED_IN)
    _assignment(db, person_id, feed_in_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix(db, [site_id])

    assert mix.prosumer_count == 1
    assert mix.consumer_count == 0
    assert mix.is_one_sided is True


def test_true_prosumer_with_both_directions_counts_on_both_sides(db):
    """A person with both a consumption- and an feed-in-MeteringPoint is
    deliberately counted in both totals -- see module docstring."""
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, None, DIRECTION_CONSUMPTION)
    feed_in_id = _metering_point(db, site_id, None, DIRECTION_FEED_IN)
    _assignment(db, person_id, consumption_id, date(2026, 1, 1))
    _assignment(db, person_id, feed_in_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix(db, [site_id])

    assert mix.prosumer_count == 1
    assert mix.consumer_count == 1
    assert mix.is_one_sided is False
    assert mix.ratio == "1:1"
    assert mix.total_persons == 2  # a true prosumer is counted on both sides, see above


def test_ended_assignment_before_reference_date_no_longer_counts(db):
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, None, DIRECTION_CONSUMPTION)
    _assignment(db, person_id, consumption_id, date(2020, 1, 1), date(2020, 12, 31))

    mix = participant_mix.compute_participant_mix(db, [site_id], reference_date=date(2026, 1, 1))

    assert mix.consumer_count == 0
    assert mix.prosumer_count == 0


def test_not_yet_started_assignment_counts_as_prosumer_and_consumer(db):
    """Real customer data surfaced this: every LEG showed 0:0 because every
    Assignment was pre-entered for the following quarter's move-ins. A
    not-yet-started Assignment is now always relevant here (`Assignment.
    is_current_or_upcoming`), not just once its start date arrives."""
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, None, DIRECTION_CONSUMPTION)
    feed_in_id = _metering_point(db, site_id, None, DIRECTION_FEED_IN)
    _assignment(db, person_id, consumption_id, date(2026, 12, 1))
    _assignment(db, person_id, feed_in_id, date(2026, 12, 1))

    mix = participant_mix.compute_participant_mix(db, [site_id], reference_date=date(2026, 9, 10))

    assert mix.consumer_count == 1
    assert mix.prosumer_count == 1


def test_empty_scope_is_not_flagged_as_one_sided(db):
    """No participants at all yet is not the same problem as one-sided --
    nothing to warn about."""
    mix = participant_mix.compute_participant_mix(db, [])

    assert mix.prosumer_count == 0
    assert mix.consumer_count == 0
    assert mix.is_one_sided is True
    assert mix.hint is None


def test_hint_nur_suppliers(db):
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    person_id = _person(db)
    feed_in_id = _metering_point(db, site_id, None, DIRECTION_FEED_IN)
    _assignment(db, person_id, feed_in_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix_for_substation_area(db, substation_area_id)

    assert "Nur Prosumer" in mix.hint


def test_hint_nur_consumers(db):
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, None, DIRECTION_CONSUMPTION)
    _assignment(db, person_id, consumption_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix_for_substation_area(db, substation_area_id)

    assert "Nur Consumer" in mix.hint


def test_upgrade_candidate_found_when_mixed_leg_and_substation_area_now_workable(db):
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    site_id = _site(db, substation_area_id)
    other_site_id = _site(db, other_substation_area_id, street="Anderswo")

    mixed_leg_id = _leg(db, "Gemischte LEG")
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, mixed_leg_id, DIRECTION_CONSUMPTION)
    feed_in_id = _metering_point(db, site_id, mixed_leg_id, DIRECTION_FEED_IN)
    _assignment(db, person_id, consumption_id, date(2026, 1, 1))
    _assignment(db, person_id, feed_in_id, date(2026, 1, 1))
    other_person_id = _person(db, "Andere")
    other_mp_id = _metering_point(db, other_site_id, mixed_leg_id, DIRECTION_CONSUMPTION)
    _assignment(db, other_person_id, other_mp_id, date(2026, 1, 1))

    candidates = participant_mix.find_upgrade_candidates(db)

    matching = [c for c in candidates if c.substation_area.id == substation_area_id]
    assert len(matching) == 1
    assert matching[0].mixed_legs[0].id == mixed_leg_id
    assert matching[0].person_count == 1


def test_no_upgrade_candidate_for_an_already_dedicated_leg(db):
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    dedicated_leg_id = _leg(db, "Dedizierte LEG")
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, dedicated_leg_id, DIRECTION_CONSUMPTION)
    feed_in_id = _metering_point(db, site_id, dedicated_leg_id, DIRECTION_FEED_IN)
    _assignment(db, person_id, consumption_id, date(2026, 1, 1))
    _assignment(db, person_id, feed_in_id, date(2026, 1, 1))

    candidates = participant_mix.find_upgrade_candidates(db)

    assert [c for c in candidates if c.substation_area.id == substation_area_id] == []


def test_upgrade_candidate_hidden_below_min_persons(db):
    """A substation area with both sides present but too few people overall is
    not suggested -- the same setup that produces a candidate with
    `min_persons=0` (the default) produces none once the threshold
    exceeds the 2 people actually present."""
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    site_id = _site(db, substation_area_id)
    other_site_id = _site(db, other_substation_area_id, street="Anderswo")

    mixed_leg_id = _leg(db, "Gemischte LEG")
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, mixed_leg_id, DIRECTION_CONSUMPTION)
    feed_in_id = _metering_point(db, site_id, mixed_leg_id, DIRECTION_FEED_IN)
    _assignment(db, person_id, consumption_id, date(2026, 1, 1))
    _assignment(db, person_id, feed_in_id, date(2026, 1, 1))
    other_person_id = _person(db, "Andere")
    other_mp_id = _metering_point(db, other_site_id, mixed_leg_id, DIRECTION_CONSUMPTION)
    _assignment(db, other_person_id, other_mp_id, date(2026, 1, 1))

    candidates = participant_mix.find_upgrade_candidates(db, min_persons=3)

    assert [c for c in candidates if c.substation_area.id == substation_area_id] == []

    candidates = participant_mix.find_upgrade_candidates(db, min_persons=2)

    assert len([c for c in candidates if c.substation_area.id == substation_area_id]) == 1


def test_no_upgrade_candidate_for_a_still_one_sided_substation_area(db):
    """Even in a mixed LEG, a substation area with only one side present is not
    an upgrade candidate -- it genuinely cannot stand alone yet."""
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    site_id = _site(db, substation_area_id)
    other_site_id = _site(db, other_substation_area_id, street="Anderswo")
    mixed_leg_id = _leg(db, "Gemischte LEG")

    person_id = _person(db)
    feed_in_id = _metering_point(db, site_id, mixed_leg_id, DIRECTION_FEED_IN)
    other_person_id = _person(db, "Andere")
    other_mp_id = _metering_point(db, other_site_id, mixed_leg_id, DIRECTION_CONSUMPTION)
    _assignment(db, person_id, feed_in_id, date(2026, 1, 1))
    _assignment(db, other_person_id, other_mp_id, date(2026, 1, 1))

    candidates = participant_mix.find_upgrade_candidates(db)

    assert [c for c in candidates if c.substation_area.id == substation_area_id] == []


def test_leg_should_split_when_every_substation_area_is_independently_green(db):
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    site_id = _site(db, substation_area_id)
    other_site_id = _site(db, other_substation_area_id, street="Anderswo")
    mixed_leg_id = _leg(db, "Gemischte LEG")

    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, mixed_leg_id, DIRECTION_CONSUMPTION)
    feed_in_id = _metering_point(db, site_id, mixed_leg_id, DIRECTION_FEED_IN)
    other_person_id = _person(db, "Andere")
    other_consumption_id = _metering_point(db, other_site_id, mixed_leg_id, DIRECTION_CONSUMPTION)
    other_feed_in_id = _metering_point(db, other_site_id, mixed_leg_id, DIRECTION_FEED_IN)
    for pid, mp_id in (
        (person_id, consumption_id),
        (person_id, feed_in_id),
        (other_person_id, other_consumption_id),
        (other_person_id, other_feed_in_id),
    ):
        _assignment(db, pid, mp_id, date(2026, 1, 1))

    assert participant_mix.leg_should_split(db, mixed_leg_id) is True


def test_leg_should_not_split_when_one_substation_area_would_be_one_sided_alone(db):
    """Splitting would strand this substation area's participants -- the LEG
    stays better off shared, even though it is mixed."""
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    site_id = _site(db, substation_area_id)
    other_site_id = _site(db, other_substation_area_id, street="Anderswo")
    mixed_leg_id = _leg(db, "Gemischte LEG")

    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, mixed_leg_id, DIRECTION_CONSUMPTION)
    feed_in_id = _metering_point(db, site_id, mixed_leg_id, DIRECTION_FEED_IN)
    # This substation area only has a producer -- would be one-sided alone.
    other_person_id = _person(db, "Andere")
    other_mp_id = _metering_point(db, other_site_id, mixed_leg_id, DIRECTION_FEED_IN)
    for pid, mp_id in ((person_id, consumption_id), (person_id, feed_in_id), (other_person_id, other_mp_id)):
        _assignment(db, pid, mp_id, date(2026, 1, 1))

    assert participant_mix.leg_should_split(db, mixed_leg_id) is False


def test_leg_should_not_split_below_min_persons(db):
    """Every substation area is independently non-one-sided (would split under
    the default `min_persons=0`), but each only has 2 people -- raising
    the threshold above that turns the recommendation off again."""
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    site_id = _site(db, substation_area_id)
    other_site_id = _site(db, other_substation_area_id, street="Anderswo")
    mixed_leg_id = _leg(db, "Gemischte LEG")

    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, mixed_leg_id, DIRECTION_CONSUMPTION)
    feed_in_id = _metering_point(db, site_id, mixed_leg_id, DIRECTION_FEED_IN)
    other_person_id = _person(db, "Andere")
    other_consumption_id = _metering_point(db, other_site_id, mixed_leg_id, DIRECTION_CONSUMPTION)
    other_feed_in_id = _metering_point(db, other_site_id, mixed_leg_id, DIRECTION_FEED_IN)
    for pid, mp_id in (
        (person_id, consumption_id),
        (person_id, feed_in_id),
        (other_person_id, other_consumption_id),
        (other_person_id, other_feed_in_id),
    ):
        _assignment(db, pid, mp_id, date(2026, 1, 1))

    assert participant_mix.leg_should_split(db, mixed_leg_id, min_persons=2) is True
    assert participant_mix.leg_should_split(db, mixed_leg_id, min_persons=3) is False


def test_leg_should_not_split_when_not_mixed(db):
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    dedicated_leg_id = _leg(db, "Dedizierte LEG")
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, dedicated_leg_id, DIRECTION_CONSUMPTION)
    feed_in_id = _metering_point(db, site_id, dedicated_leg_id, DIRECTION_FEED_IN)
    _assignment(db, person_id, consumption_id, date(2026, 1, 1))
    _assignment(db, person_id, feed_in_id, date(2026, 1, 1))

    assert participant_mix.leg_should_split(db, dedicated_leg_id) is False
