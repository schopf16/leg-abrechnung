"""Tests for app.domain.participant_mix (Producer:Consumer ratio,
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
    assert mix.producer_count == 0
    assert mix.is_one_sided is True


def test_producer_counted_for_feed_in_person(db):
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    person_id = _person(db)
    feed_in_id = _metering_point(db, site_id, None, DIRECTION_FEED_IN)
    _assignment(db, person_id, feed_in_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix(db, [site_id])

    assert mix.producer_count == 1
    assert mix.consumer_count == 0
    assert mix.is_one_sided is True


def test_true_prosumer_with_both_directions_counts_on_both_sides(db):
    """A person with both a consumption- and an feed-in-MeteringPoint is deliberately counted in both..."""
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, None, DIRECTION_CONSUMPTION)
    feed_in_id = _metering_point(db, site_id, None, DIRECTION_FEED_IN)
    _assignment(db, person_id, consumption_id, date(2026, 1, 1))
    _assignment(db, person_id, feed_in_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix(db, [site_id])

    assert mix.producer_count == 1
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
    assert mix.producer_count == 0


def test_not_yet_started_assignment_counts_as_producer_and_consumer(db):
    """Real customer data surfaced this: every LEG showed 0:0 because every Assignment was pre-entered..."""
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, None, DIRECTION_CONSUMPTION)
    feed_in_id = _metering_point(db, site_id, None, DIRECTION_FEED_IN)
    _assignment(db, person_id, consumption_id, date(2026, 12, 1))
    _assignment(db, person_id, feed_in_id, date(2026, 12, 1))

    mix = participant_mix.compute_participant_mix(db, [site_id], reference_date=date(2026, 9, 10))

    assert mix.consumer_count == 1
    assert mix.producer_count == 1


def test_empty_scope_is_not_flagged_as_one_sided(db):
    """No participants at all yet is not the same problem as one-sided -- nothing to warn about."""
    mix = participant_mix.compute_participant_mix(db, [])

    assert mix.producer_count == 0
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

    assert "Nur Produzenten" in mix.hint


def test_hint_nur_consumers(db):
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site(db, substation_area_id)
    person_id = _person(db)
    consumption_id = _metering_point(db, site_id, None, DIRECTION_CONSUMPTION)
    _assignment(db, person_id, consumption_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix_for_substation_area(db, substation_area_id)

    assert "Nur Konsumenten" in mix.hint


def test_the_two_sides_add_up_to_the_metering_point_count(db):
    """What a reader checks first: 9 + 26 has to be the 35 shown beside it."""
    leg_id = _leg(db, "LEG")
    area_id = _substation_area(db, "TRA")
    site_id = _site(db, area_id)
    person_id = _person(db)

    for _ in range(3):
        mp = _metering_point(db, site_id, leg_id, DIRECTION_FEED_IN)
        _assignment(db, person_id, mp, date(2025, 1, 1))
    for _ in range(5):
        mp = _metering_point(db, site_id, leg_id, DIRECTION_CONSUMPTION)
        _assignment(db, person_id, mp, date(2025, 1, 1))

    mix = participant_mix.compute_participant_mix_for_leg(db, leg_id)

    assert mix.producer_metering_points + mix.consumer_metering_points == leg_repo.count_metering_points(
        db, leg_id
    )
    assert mix.ratio == "3:5"
    # One person holds all eight, so the person counts are 1:1 -- which is
    # exactly why the badge must not show them.
    assert (mix.producer_count, mix.consumer_count) == (1, 1)


def test_a_metering_point_without_a_current_assignment_still_counts(db):
    """It belongs to the LEG and it has a direction, so it is not invisible."""
    leg_id = _leg(db, "LEG")
    site_id = _site(db, _substation_area(db, "TRA"))
    person_id = _person(db)

    assigned = _metering_point(db, site_id, leg_id, DIRECTION_CONSUMPTION)
    _assignment(db, person_id, assigned, date(2025, 1, 1))
    _metering_point(db, site_id, leg_id, DIRECTION_CONSUMPTION)  # never assigned
    moved_out = _metering_point(db, site_id, leg_id, DIRECTION_FEED_IN)
    _assignment(db, person_id, moved_out, date(2020, 1, 1), date(2020, 12, 31))

    mix = participant_mix.compute_participant_mix_for_leg(db, leg_id)

    assert mix.consumer_metering_points == 2
    assert mix.producer_metering_points == 1
    assert mix.unassigned_metering_points == 2, "beide müssen gesondert ausgewiesen sein"
    # The people are counted as before: nobody currently holds the other two.
    assert (mix.producer_count, mix.consumer_count) == (0, 1)


def test_a_leg_counts_its_own_metering_points_not_its_neighbours(db):
    """LEG membership hangs on the metering point, not on the address."""
    area_id = _substation_area(db, "TRA")
    site_id = _site(db, area_id)
    ours, theirs = _leg(db, "Unsere"), _leg(db, "Fremde")
    person_id = _person(db)

    mine = _metering_point(db, site_id, ours, DIRECTION_CONSUMPTION)
    _assignment(db, person_id, mine, date(2025, 1, 1))
    not_mine = _metering_point(db, site_id, theirs, DIRECTION_FEED_IN)
    _assignment(db, person_id, not_mine, date(2025, 1, 1))

    mix = participant_mix.compute_participant_mix_for_leg(db, ours)

    assert mix.consumer_metering_points == 1
    assert mix.producer_metering_points == 0, "der Messpunkt der anderen LEG gehört nicht dazu"
    assert mix.producer_metering_points + mix.consumer_metering_points == (
        leg_repo.count_metering_points(db, ours)
    )


def test_the_person_count_still_counts_people(db):
    """Seven meters are not seven members."""
    area_id = _substation_area(db, "TRA")
    site_id = _site(db, area_id)
    leg_id = _leg(db, "LEG")
    person_id = _person(db)

    for direction in (DIRECTION_FEED_IN, *[DIRECTION_CONSUMPTION] * 7):
        mp = _metering_point(db, site_id, leg_id, direction)
        _assignment(db, person_id, mp, date(2025, 1, 1))

    mix = participant_mix.compute_participant_mix_for_substation_area(db, area_id)

    assert mix.producer_metering_points + mix.consumer_metering_points == 8
    assert mix.total_persons == 2, "eine Person, auf beiden Seiten gezählt"


# --- Connections by side, for the overview tiles ------------------------
#
# The unit is a **connection**: one party at one location. Counting people
# instead was wrong in both directions -- a property management with two
# buildings collapsed into one Prosumer, and a Mehrfamilienhaus would have
# collapsed its tenants into its site. The administrator found it by
# adding the tiles up against the metering-point count and coming out two
# short.


def test_one_party_at_two_locations_is_two_connections(db):
    """The case that exposed the unit."""
    leg_id = _leg(db, "LEG")
    area_id = _substation_area(db, "TRA")
    company = _person(db, "Verwaltung")
    for street in ("Erstweg", "Zweitweg"):
        site_id = _site(db, area_id, street=street)
        for direction in (DIRECTION_FEED_IN, DIRECTION_CONSUMPTION):
            mp = _metering_point(db, site_id, leg_id, direction)
            _assignment(db, company, mp, date(2025, 1, 1))

    roles = participant_mix.compute_participant_roles(db)

    assert roles.prosumers == 2, "zwei Liegenschaften sind zwei Anschlüsse"
    assert roles.consumers == 0
    assert roles.total == 2


def test_several_parties_at_one_location_stay_separate(db):
    """A Mehrfamilienhaus is not one Prosumer."""
    leg_id = _leg(db, "LEG")
    site_id = _site(db, _substation_area(db, "TRA"))
    owner = _person(db, "Eigentuemerin")
    _assignment(db, owner, _metering_point(db, site_id, leg_id, DIRECTION_FEED_IN), date(2025, 1, 1))
    _assignment(db, owner, _metering_point(db, site_id, leg_id, DIRECTION_CONSUMPTION), date(2025, 1, 1))
    for name in ("Mieter1", "Mieter2"):
        tenant = _person(db, name)
        mp = _metering_point(db, site_id, leg_id, DIRECTION_CONSUMPTION)
        _assignment(db, tenant, mp, date(2025, 1, 1))

    roles = participant_mix.compute_participant_roles(db)

    assert roles.prosumers == 1
    assert roles.consumers == 2, "jede Partei zählt für sich"
    assert roles.total == 3


def test_the_two_sides_account_for_every_metering_point(db):
    """The arithmetic the administrator actually performs."""
    leg_id = _leg(db, "LEG")
    area_id = _substation_area(db, "TRA")
    company = _person(db, "Verwaltung")
    for street in ("Erstweg", "Zweitweg"):
        site_id = _site(db, area_id, street=street)
        for direction in (DIRECTION_FEED_IN, DIRECTION_CONSUMPTION):
            _assignment(db, company, _metering_point(db, site_id, leg_id, direction), date(2025, 1, 1))
    lone = _person(db, "NurEinspeisung")
    lone_site = _site(db, area_id, street="Solarweg")
    _assignment(db, lone, _metering_point(db, lone_site, leg_id, DIRECTION_FEED_IN), date(2025, 1, 1))
    drawer = _person(db, "NurBezug")
    drawer_site = _site(db, area_id, street="Wohnweg")
    _assignment(db, drawer, _metering_point(db, drawer_site, leg_id, DIRECTION_CONSUMPTION), date(2025, 1, 1))

    roles = participant_mix.compute_participant_roles(db)
    both = roles.prosumers - len(roles.feed_in_only)
    expected = both * 2 + len(roles.feed_in_only) + roles.consumers
    actual = len(metering_point_repo.list_all(db))

    assert expected == actual == 6


def test_a_connection_that_only_feeds_in_is_a_prosumer_and_is_named(db):
    """The administrator's model: whoever feeds in also draws there."""
    leg_id = _leg(db, "LEG")
    site_id = _site(db, _substation_area(db, "TRA"))
    person_id = _person(db, "NurEinspeisung")
    _assignment(db, person_id, _metering_point(db, site_id, leg_id, DIRECTION_FEED_IN), date(2025, 1, 1))

    roles = participant_mix.compute_participant_roles(db)

    assert roles.prosumers == 1
    assert roles.consumers == 0
    assert roles.feed_in_only == [(person_id, site_id)]


def test_two_feed_in_meters_at_one_location_are_still_one_connection(db):
    """Two arrays on one roof do not make two Prosumer."""
    leg_id = _leg(db, "LEG")
    site_id = _site(db, _substation_area(db, "TRA"))
    person_id = _person(db, "ZweiAnlagen")
    for direction in (DIRECTION_FEED_IN, DIRECTION_FEED_IN, DIRECTION_CONSUMPTION):
        _assignment(db, person_id, _metering_point(db, site_id, leg_id, direction), date(2025, 1, 1))

    roles = participant_mix.compute_participant_roles(db)

    assert roles.prosumers == 1
    assert roles.total == 1
    assert roles.feed_in_only == []


def test_a_person_without_any_assignment_is_in_neither_count(db):
    """Somebody merely recorded is not yet taking part."""
    _person(db, "Ohne")

    assert participant_mix.compute_participant_roles(db).total == 0


def test_an_ended_assignment_no_longer_counts(db):
    """Same reference rule as the rest of this module."""
    leg_id = _leg(db, "LEG")
    site_id = _site(db, _substation_area(db, "TRA"))
    person_id = _person(db, "Ausgezogen")
    mp = _metering_point(db, site_id, leg_id, DIRECTION_CONSUMPTION)
    _assignment(db, person_id, mp, date(2024, 1, 1), date(2024, 12, 31))

    roles = participant_mix.compute_participant_roles(db, reference_date=date(2026, 1, 1))

    assert roles.total == 0


def test_the_tiles_show_both_counts_and_admit_the_gaps():
    """Rendered, not just computed -- and the caption has to stay true."""
    from nicegui import Client, ui

    from app.db.connection import connection_scope
    from app.gui.pages import dashboard as dashboard_module

    with connection_scope() as connection:
        leg_id = _leg(connection, "LEG")
        area_id = _substation_area(connection, "TRA")
        both = _person(connection, "Beides")
        both_site = _site(connection, area_id, street="Beidesweg")
        for direction in (DIRECTION_FEED_IN, DIRECTION_CONSUMPTION):
            _assignment(
                connection, both, _metering_point(connection, both_site, leg_id, direction), date(2025, 1, 1)
            )
        lone = _person(connection, "NurEinspeisung")
        lone_site = _site(connection, area_id, street="Solarweg")
        _assignment(
            connection,
            lone,
            _metering_point(connection, lone_site, leg_id, DIRECTION_FEED_IN),
            date(2025, 1, 1),
        )
        drawer = _person(connection, "NurBezug")
        drawer_site = _site(connection, area_id, street="Wohnweg")
        _assignment(
            connection,
            drawer,
            _metering_point(connection, drawer_site, leg_id, DIRECTION_CONSUMPTION),
            date(2025, 1, 1),
        )

    client = Client(ui.page("/probe-roles")(lambda: None), request=None)
    with client:
        dashboard_module.dashboard_page()

    texts = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", None)
    ]
    assert "Prosumer" in texts
    assert "Konsumer" in texts
    assert "Anschlüsse mit Einspeisung und Bezug" in texts
    assert "Anschlüsse nur mit Bezug" in texts
    assert "davon 1 ohne Bezug" in texts
