"""Tests for `app.domain.statistics.leg_balance`."""

from datetime import datetime, timedelta

from app.domain.statistics import leg_balance, shared_energy_by_leg
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.site import Site
from app.models.substation_area import SubstationArea

#: A Wednesday, so nothing in the fixtures lands on a month or year edge.
_START = datetime(2026, 4, 15, 0, 0)


def _leg(db, name: str) -> int:
    """Create a LEG."""
    return leg_repo.create(db, Leg(id=None, name=name, note="", created_at=""))


def _site(db, street: str) -> int:
    """Create a site in its own Trafokreis."""
    area_id = substation_area_repo.create(
        db, SubstationArea(id=None, name=street, bkw_designation="", note="", created_at="")
    )
    return site_repo.create(
        db,
        Site(
            id=None,
            street=street,
            house_number="1",
            postal_code="3063",
            municipality="Ittigen",
            address_detail="",
            substation_area_id=area_id,
            created_at="",
        ),
    )


def _meter(db, leg_id: int, site_id: int, direction: str, designation: str) -> int:
    """Create a metering point in one LEG."""
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


def _readings(db, metering_point_id: int, direction: str, values: list[float]) -> None:
    """Store consecutive 15-minute readings starting at `_START`."""
    db.executemany(
        "INSERT INTO readings (metering_point_id, timestamp, direction, kwh, source) VALUES (?, ?, ?, ?, ?)",
        [
            (
                metering_point_id,
                (_START + timedelta(minutes=15 * index)).isoformat(),
                direction,
                value,
                "test",
            )
            for index, value in enumerate(values)
        ],
    )
    db.commit()


def test_shared_energy_is_formed_per_interval_not_from_the_totals(db):
    """The expensive mistake, kept out by a test rather than by care."""
    leg_id = _leg(db, "LEG-Eins")
    site_id = _site(db, "Erstweg")
    producer = _meter(db, leg_id, site_id, DIRECTION_FEED_IN, "CH-E-1")
    consumer = _meter(db, leg_id, site_id, DIRECTION_CONSUMPTION, "CH-B-1")
    _readings(db, producer, DIRECTION_FEED_IN, [10.0, 10.0, 0.0, 0.0])
    _readings(db, consumer, DIRECTION_CONSUMPTION, [0.0, 0.0, 10.0, 10.0])

    consumption, feed_in, shared = shared_energy_by_leg(db)[leg_id]

    assert (consumption, feed_in) == (20.0, 20.0)
    assert shared == 0.0


def test_two_legs_never_share_with_each_other(db):
    """The other half of the rule, and just as easy to get wrong."""
    producing = _leg(db, "LEG-Produktion")
    drawing = _leg(db, "LEG-Bezug")
    site_id = _site(db, "Zweitweg")
    producer = _meter(db, producing, site_id, DIRECTION_FEED_IN, "CH-E-2")
    consumer = _meter(db, drawing, site_id, DIRECTION_CONSUMPTION, "CH-B-2")
    _readings(db, producer, DIRECTION_FEED_IN, [10.0, 10.0, 10.0, 10.0])
    _readings(db, consumer, DIRECTION_CONSUMPTION, [10.0, 10.0, 10.0, 10.0])

    energy = shared_energy_by_leg(db)

    assert energy[producing][2] == 0.0
    assert energy[drawing][2] == 0.0


def test_the_shared_share_is_what_found_a_taker(db):
    """Half the production met demand, so half of it was shared."""
    leg_id = _leg(db, "LEG-Haelfte")
    site_id = _site(db, "Drittweg")
    producer = _meter(db, leg_id, site_id, DIRECTION_FEED_IN, "CH-E-3")
    consumer = _meter(db, leg_id, site_id, DIRECTION_CONSUMPTION, "CH-B-3")
    _readings(db, producer, DIRECTION_FEED_IN, [10.0, 10.0])
    _readings(db, consumer, DIRECTION_CONSUMPTION, [10.0, 0.0])

    balance = next(b for b in leg_balance(db) if b.leg_id == leg_id)

    assert balance.shared_kwh == 10.0
    assert balance.shared_share_of_production == 50.0
    assert balance.local_coverage == 100.0


def test_a_leg_without_readings_reports_no_percentage_rather_than_zero(db):
    """ "Nothing produced" and "produced, nobody took it" are not the same."""
    leg_id = _leg(db, "LEG-Ohne-Daten")
    site_id = _site(db, "Viertweg")
    _meter(db, leg_id, site_id, DIRECTION_FEED_IN, "CH-E-4")
    _meter(db, leg_id, site_id, DIRECTION_CONSUMPTION, "CH-B-4")

    balance = next(b for b in leg_balance(db) if b.leg_id == leg_id)

    assert balance.has_readings is False
    assert balance.shared_share_of_production is None
    assert balance.local_coverage is None
    assert balance.producers_per_consumer == 1.0


def test_the_legs_are_ordered_from_production_heavy_to_consumption_heavy(db):
    """One continuum, both extremes at the ends."""
    site_id = _site(db, "Fuenftweg")
    only_producers = _leg(db, "LEG-A-nur-Produktion")
    heavy = _leg(db, "LEG-B-produktionslastig")
    balanced = _leg(db, "LEG-C-ausgeglichen")
    only_consumers = _leg(db, "LEG-D-nur-Bezug")

    for index in range(2):
        _meter(db, only_producers, site_id, DIRECTION_FEED_IN, f"CH-A-E{index}")
    for index in range(4):
        _meter(db, heavy, site_id, DIRECTION_FEED_IN, f"CH-B-E{index}")
    _meter(db, heavy, site_id, DIRECTION_CONSUMPTION, "CH-B-B0")
    _meter(db, balanced, site_id, DIRECTION_FEED_IN, "CH-C-E0")
    _meter(db, balanced, site_id, DIRECTION_CONSUMPTION, "CH-C-B0")
    for index in range(3):
        _meter(db, only_consumers, site_id, DIRECTION_CONSUMPTION, f"CH-D-B{index}")

    assert [b.leg_id for b in leg_balance(db)] == [only_producers, heavy, balanced, only_consumers]


def test_an_empty_leg_comes_last_and_says_so(db):
    """It is neither end of the scale, so it does not belong on it."""
    populated = _leg(db, "LEG-Mit-Messpunkten")
    empty = _leg(db, "LEG-Leer")
    site_id = _site(db, "Sechstweg")
    _meter(db, populated, site_id, DIRECTION_FEED_IN, "CH-P-E0")
    _meter(db, populated, site_id, DIRECTION_CONSUMPTION, "CH-P-B0")

    balances = leg_balance(db)

    assert balances[-1].leg_id == empty
    assert balances[-1].one_sided_note is None
    assert balances[-1].metering_points == 0


def test_a_one_sided_leg_states_the_missing_side(db):
    """A fact, and the one statement that survived the removed advice."""
    producers_only = _leg(db, "LEG-Nur-Produktion")
    consumers_only = _leg(db, "LEG-Nur-Bezug")
    site_id = _site(db, "Siebtweg")
    _meter(db, producers_only, site_id, DIRECTION_FEED_IN, "CH-X-E0")
    _meter(db, consumers_only, site_id, DIRECTION_CONSUMPTION, "CH-Y-B0")

    notes = {b.leg_id: b.one_sided_note for b in leg_balance(db)}

    assert notes[producers_only] == "nur Produzenten"
    assert notes[consumers_only] == "nur Konsumenten"


def test_the_meter_counts_come_from_the_same_source_as_the_verteilung_view(db):
    """Two views of the same LEG must not disagree about its size."""
    from app.domain.statistics import distribution_by_leg

    leg_id = _leg(db, "LEG-Quelle")
    site_id = _site(db, "Achtweg")
    _meter(db, leg_id, site_id, DIRECTION_FEED_IN, "CH-Q-E0")
    for index in range(3):
        _meter(db, leg_id, site_id, DIRECTION_CONSUMPTION, f"CH-Q-B{index}")

    balance = next(b for b in leg_balance(db) if b.leg_id == leg_id)
    distribution = next(d for d in distribution_by_leg(db) if d.leg_id == leg_id)

    assert balance.producer_metering_points == distribution.feed_in_metering_points
    assert balance.consumer_metering_points == distribution.consumption_metering_points
