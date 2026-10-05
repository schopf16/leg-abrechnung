"""Generates demo/test data: one substation area, one LEG, four sites, seven metering points, five
persons (including a mid-quarter move), and synthetic 15-minute readings for one winter and one
summer quarter."""

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.period import INTERVAL_MINUTES, quarter_bounds
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models import assignment as assignment_repo
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.person import Person
from app.models.reading import Reading, upsert_readings
from app.models.site import Site
from app.models.substation_area import SubstationArea
from app.models.assignment import Assignment

#: Demo QR-IBAN (valid checksum, QR-IID range) so generated demo data can
#: be used to produce QR-invoices end to end without manual configuration.
_DEMO_QR_IBAN = "CH5730000123456789012"

#: Demo admin surcharges and paper-invoice fee, matching realistic
#: real-world magnitudes (see `app.domain.billing`).
_DEMO_ADMIN_FEE_CONSUMPTION_RP_PER_KWH = 0.5
_DEMO_ADMIN_FEE_FEED_IN_RP_PER_KWH = 0.5
_DEMO_PAPER_INVOICE_RAPPEN = 200

#: Year used for the generated demo quarters. Chosen in the past so both
#: quarters are always complete, regardless of when the app is run.
DEMO_YEAR = 2025

#: (year, quarter) for the winter fixture: no local feed-in.
WINTER_QUARTER = (DEMO_YEAR, 4)

#: (year, quarter) for the summer fixture: feed-in sometimes exceeds,
#: sometimes falls short of, consumption.
SUMMER_QUARTER = (DEMO_YEAR, 3)

#: Marker used to detect "demo data already created" and to keep the
#: generator idempotent. Combined (see `Person.full_name`) this reads
#: "Anna Muster (Demo)", same as before the first-/last-name split.
_DEMO_MARKER_FIRST_NAME = "Anna"
_DEMO_MARKER_LAST_NAME = "Muster (Demo)"

#: Typical household load shape, average kW per hour-of-day (index 0-23).
_HOURLY_LOAD_KW = [
    0.30,
    0.25,
    0.20,
    0.20,
    0.20,
    0.25,
    0.40,
    0.70,
    0.60,
    0.50,
    0.45,
    0.45,
    0.50,
    0.45,
    0.40,
    0.45,
    0.55,
    0.75,
    0.90,
    0.85,
    0.70,
    0.55,
    0.45,
    0.35,
]

#: Weekly multiplier (Mon=0 .. Sun=6) giving slightly higher weekend use.
_WEEKDAY_FACTOR = [1.0, 1.0, 1.0, 1.0, 1.05, 1.2, 1.15]

#: Per-day-of-quarter solar scale cycling through cloudy/mixed/sunny days,
#: chosen so that both `P(t) > C(t)` and `P(t) < C(t)` occur in summer.
_SOLAR_DAY_SCALE = [0.5, 1.0, 1.4]


@dataclass
class DemoDataSummary:
    """Result of a successful demo data generation run."""

    person_ids: list[int]
    metering_point_ids: list[int]
    reading_count: int


class DemoDataAlreadyExists(Exception):
    """Raised when demo data generation is requested but already ran."""


def demo_data_exists(connection: sqlite3.Connection) -> bool:
    """Check whether the demo data set has already been created."""
    row = connection.execute(
        "SELECT 1 FROM person WHERE first_name = ? AND last_name = ?",
        (_DEMO_MARKER_FIRST_NAME, _DEMO_MARKER_LAST_NAME),
    ).fetchone()
    return row is not None


def _consumption_kwh(moment: datetime, scale: float) -> float:
    """Compute a synthetic consumption value for one 15-minute interval."""
    kw = _HOURLY_LOAD_KW[moment.hour] * _WEEKDAY_FACTOR[moment.weekday()] * scale
    return round(kw * (INTERVAL_MINUTES / 60), 3)


def _production_kwh(moment: datetime, scale: float, day_index: int) -> float:
    """Compute a synthetic feed-in value for one 15-minute interval."""
    hour = moment.hour + moment.minute / 60
    if hour < 6 or hour > 20:
        return 0.0
    bell = math.sin(math.pi * (hour - 6) / 14)
    day_scale = _SOLAR_DAY_SCALE[day_index % len(_SOLAR_DAY_SCALE)]
    kw = bell * scale * day_scale
    return round(max(kw, 0.0) * (INTERVAL_MINUTES / 60), 3)


def _generate_readings_for_quarter(
    metering_point_id: int,
    direction: str,
    scale: float,
    year: int,
    quarter: int,
    feed_in_disabled: bool,
) -> list[Reading]:
    """Generate one quarter's worth of 15-minute synthetic readings."""
    start, end = quarter_bounds(year, quarter)
    is_feed_in = direction == DIRECTION_FEED_IN

    readings: list[Reading] = []
    moment = start
    while moment < end:
        day_index = (moment.date() - start.date()).days
        if is_feed_in:
            kwh = 0.0 if feed_in_disabled else _production_kwh(moment, scale, day_index)
        else:
            kwh = _consumption_kwh(moment, scale)
        readings.append(
            Reading(
                metering_point_id=metering_point_id,
                timestamp=moment.isoformat(),
                direction=direction,
                kwh=kwh,
                source="demo",
                import_batch_id=None,
            )
        )
        moment += timedelta(minutes=INTERVAL_MINUTES)
    return readings


def create_demo_data(connection: sqlite3.Connection) -> DemoDataSummary:
    """Create the full demo data set: LEG, sites, metering points, persons, assignments, readings."""
    if demo_data_exists(connection):
        raise DemoDataAlreadyExists(
            "Demo-Daten wurden bereits erzeugt (Person "
            f'"{_DEMO_MARKER_FIRST_NAME} {_DEMO_MARKER_LAST_NAME}" existiert schon).'
        )

    substation_area = _create_demo_substation_area(connection)
    leg = _create_demo_leg(connection)
    sites = _create_demo_sites(connection, substation_area)
    metering_points = _create_demo_metering_points(connection, sites, leg)
    persons = _create_demo_persons(connection)
    _create_demo_assignments(connection, persons, metering_points)
    reading_count = _create_demo_readings(connection, metering_points)
    _set_demo_leg_settings(connection)

    return DemoDataSummary(
        person_ids=[p.id for p in persons.values()],
        metering_point_ids=[mp.id for mp in metering_points.values()],
        reading_count=reading_count,
    )


def _create_demo_substation_area(connection: sqlite3.Connection) -> SubstationArea:
    """Insert the single demo substation area all demo sites share."""
    substation_area = SubstationArea(
        id=None,
        name="Bern_TRA00001",
        bkw_designation="TRA00001",
        note="",
        created_at="",
    )
    substation_area.id = substation_area_repo.create(connection, substation_area)
    return substation_area


def _create_demo_leg(connection: sqlite3.Connection) -> Leg:
    """Insert the single demo LEG all demo metering points share."""
    leg = Leg(
        id=None,
        name="Bern_TRA00001",
        note="",
        created_at="",
    )
    leg.id = leg_repo.create(connection, leg)
    return leg


def _create_demo_sites(connection: sqlite3.Connection, substation_area: SubstationArea) -> dict[str, Site]:
    """Insert the four demo sites, all on the demo substation area."""
    definitions = {
        "anna": Site(
            id=None,
            street="Sonnenweg",
            house_number="1",
            postal_code="3000",
            municipality="Bern",
            address_detail="",
            substation_area_id=substation_area.id,
            created_at="",
        ),
        "beat": Site(
            id=None,
            street="Sonnenweg",
            house_number="2",
            postal_code="3000",
            municipality="Bern",
            address_detail="",
            substation_area_id=substation_area.id,
            created_at="",
        ),
        "carla": Site(
            id=None,
            street="Bergstrasse",
            house_number="3",
            postal_code="3001",
            municipality="Bern",
            address_detail="",
            substation_area_id=substation_area.id,
            created_at="",
        ),
        "bergstrasse4": Site(
            id=None,
            street="Bergstrasse",
            house_number="4",
            postal_code="3001",
            municipality="Bern",
            address_detail="",
            substation_area_id=substation_area.id,
            created_at="",
        ),
        # The two properties of the demo Verwaltung. Deliberately on
        # different streets, so a bill covering both shows the address
        # ordering (Lindenweg before Parkstrasse) as well as the grouping.
        "lindenweg7": Site(
            id=None,
            street="Lindenweg",
            house_number="7",
            postal_code="3001",
            municipality="Bern",
            address_detail="",
            substation_area_id=substation_area.id,
            created_at="",
        ),
        "parkstrasse12": Site(
            id=None,
            street="Parkstrasse",
            house_number="12",
            postal_code="3001",
            municipality="Bern",
            address_detail="",
            substation_area_id=substation_area.id,
            created_at="",
        ),
    }
    created = {}
    for handle, site in definitions.items():
        site.id = site_repo.create(connection, site)
        created[handle] = site
    return created


def _create_demo_metering_points(
    connection: sqlite3.Connection, sites: dict[str, Site], leg: Leg
) -> dict[str, MeteringPoint]:
    """Insert the demo metering points for the showcase sites, all on the demo LEG."""
    definitions = {
        "anna_bezug": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000001",
            direction=DIRECTION_CONSUMPTION,
            site_id=sites["anna"].id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
        "anna_einspeisung": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000002",
            direction=DIRECTION_FEED_IN,
            site_id=sites["anna"].id,
            leg_id=leg.id,
            pv_capacity_kwp=6.4,
            battery_capacity_kwh=None,
            created_at="",
        ),
        "beat_bezug": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000003",
            direction=DIRECTION_CONSUMPTION,
            site_id=sites["beat"].id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
        "beat_einspeisung": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000004",
            direction=DIRECTION_FEED_IN,
            site_id=sites["beat"].id,
            leg_id=leg.id,
            pv_capacity_kwp=9.9,
            battery_capacity_kwh=10.0,
            created_at="",
        ),
        "carla_bezug_1": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000005",
            direction=DIRECTION_CONSUMPTION,
            site_id=sites["carla"].id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
            label="Büro",
        ),
        "carla_bezug_2": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000006",
            direction=DIRECTION_CONSUMPTION,
            site_id=sites["carla"].id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
            label="Werkstatt",
        ),
        "bergstrasse4_bezug": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000007",
            direction=DIRECTION_CONSUMPTION,
            site_id=sites["bergstrasse4"].id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
        # Lindenweg 7: three flats plus the common supply and a PV roof.
        # The labels are the whole point -- a Verwaltung allocating this
        # bill internally cannot do anything with "CH10000...".
        "lindenweg7_allgemein": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000008",
            direction=DIRECTION_CONSUMPTION,
            site_id=sites["lindenweg7"].id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
            label="Allgemeinstrom",
        ),
        "lindenweg7_whg1": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000009",
            direction=DIRECTION_CONSUMPTION,
            site_id=sites["lindenweg7"].id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
            label="Whg. 1. OG",
        ),
        "lindenweg7_whg2": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000010",
            direction=DIRECTION_CONSUMPTION,
            site_id=sites["lindenweg7"].id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
            label="Whg. 2. OG",
        ),
        "lindenweg7_pv": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000011",
            direction=DIRECTION_FEED_IN,
            site_id=sites["lindenweg7"].id,
            leg_id=leg.id,
            pv_capacity_kwp=18.5,
            battery_capacity_kwh=None,
            created_at="",
            label="PV Dach",
        ),
        # Parkstrasse 12: consumption only, so the second block on the
        # bill has just one section -- and one metering point is left
        # unlabelled on purpose, which is what most of them look like.
        "parkstrasse12_allgemein": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000012",
            direction=DIRECTION_CONSUMPTION,
            site_id=sites["parkstrasse12"].id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
            label="Allgemeinstrom",
        ),
        "parkstrasse12_whg_eg": MeteringPoint(
            id=None,
            designation="CH1000000000000000000000013",
            direction=DIRECTION_CONSUMPTION,
            site_id=sites["parkstrasse12"].id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
    }
    created = {}
    for handle, metering_point in definitions.items():
        metering_point.id = metering_point_repo.create(connection, metering_point)
        created[handle] = metering_point
    return created


def _create_demo_persons(connection: sqlite3.Connection) -> dict[str, Person]:
    """Insert the four showcase persons plus one "previous tenant"."""
    definitions = {
        "anna": Person(
            id=None,
            salutation="Frau",
            company="",
            first_name=_DEMO_MARKER_FIRST_NAME,
            last_name=_DEMO_MARKER_LAST_NAME,
            contact_email="anna.muster@example.ch",
            contact_phone="",
            billing_street="Sonnenweg",
            billing_house_number="1",
            billing_postal_code="3000",
            billing_city="Bern",
            billing_country="CH",
            iban="CH9300762011623852957",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
        "beat": Person(
            id=None,
            salutation="Herr",
            company="",
            first_name="Beat",
            last_name="Beispiel (Demo)",
            contact_email="beat.beispiel@example.ch",
            contact_phone="",
            billing_street="Sonnenweg",
            billing_house_number="2",
            billing_postal_code="3000",
            billing_city="Bern",
            billing_country="CH",
            iban="CH5604835012345678009",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=True,
            active=True,
            created_at="",
        ),
        "carla": Person(
            id=None,
            salutation="Frau",
            company="Consumer AG (Demo)",
            first_name="Carla",
            last_name="Consumer",
            contact_email="carla.consumer@example.ch",
            contact_phone="",
            billing_street="Bergstrasse",
            billing_house_number="3",
            billing_postal_code="3001",
            billing_city="Bern",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
        "david": Person(
            id=None,
            salutation="Herr",
            company="",
            first_name="David",
            last_name="Demo (Demo)",
            contact_email="david.demo@example.ch",
            contact_phone="",
            billing_street="Bergstrasse",
            billing_house_number="4",
            billing_postal_code="3001",
            billing_city="Bern",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
        "verwaltung": Person(
            id=None,
            salutation="Frau",
            company="Hauswerk Verwaltungs AG (Demo)",
            first_name="Nadia",
            last_name="Verwalterin",
            contact_email="nadia.verwalterin@example.ch",
            contact_phone="",
            billing_street="Marktgasse",
            billing_house_number="20",
            billing_postal_code="3001",
            billing_city="Bern",
            billing_country="CH",
            iban="CH9300762011623852957",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
        "erika": Person(
            id=None,
            salutation="Frau",
            company="",
            first_name="Erika",
            last_name="Vorgängerin (Demo, Umzug-Beispiel)",
            contact_email="",
            contact_phone="",
            billing_street="Bergstrasse",
            billing_house_number="4",
            billing_postal_code="3001",
            billing_city="Bern",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
    }
    created = {}
    for handle, person in definitions.items():
        person.id = person_repo.create(connection, person)
        created[handle] = person
    return created


def _create_demo_assignments(
    connection: sqlite3.Connection,
    persons: dict[str, Person],
    metering_points: dict[str, MeteringPoint],
) -> None:
    """Insert assignments, including the mid-quarter move example."""
    summer_start, _ = quarter_bounds(*SUMMER_QUARTER)
    move_date = date(2025, 8, 16)

    static_assignments = [
        ("anna_bezug", "anna"),
        ("anna_einspeisung", "anna"),
        ("beat_bezug", "beat"),
        ("beat_einspeisung", "beat"),
        ("carla_bezug_1", "carla"),
        ("carla_bezug_2", "carla"),
        # One customer, six metering points, two properties -- the whole
        # point of the Verwaltung case: it still yields ONE bill.
        ("lindenweg7_allgemein", "verwaltung"),
        ("lindenweg7_whg1", "verwaltung"),
        ("lindenweg7_whg2", "verwaltung"),
        ("lindenweg7_pv", "verwaltung"),
        ("parkstrasse12_allgemein", "verwaltung"),
        ("parkstrasse12_whg_eg", "verwaltung"),
    ]
    for metering_point_handle, person_handle in static_assignments:
        assignment_repo.create(
            connection,
            Assignment(
                id=None,
                person_id=persons[person_handle].id,
                metering_point_id=metering_points[metering_point_handle].id,
                valid_from=summer_start.date(),
                valid_to=None,
                created_at="",
            ),
        )

    # The move: Erika until the day before the move, David from the move on.
    assignment_repo.create(
        connection,
        Assignment(
            id=None,
            person_id=persons["erika"].id,
            metering_point_id=metering_points["bergstrasse4_bezug"].id,
            valid_from=summer_start.date(),
            valid_to=move_date - timedelta(days=1),
            created_at="",
        ),
    )
    assignment_repo.create(
        connection,
        Assignment(
            id=None,
            person_id=persons["david"].id,
            metering_point_id=metering_points["bergstrasse4_bezug"].id,
            valid_from=move_date,
            valid_to=None,
            created_at="",
        ),
    )


#: Per-MeteringPoint scale factors used for both consumption and feed-in shape.
_METERING_POINT_SCALES = {
    "anna_bezug": 1.0,
    "anna_einspeisung": 4.0,
    "beat_bezug": 1.3,
    "beat_einspeisung": 3.0,
    "carla_bezug_1": 0.8,
    "carla_bezug_2": 1.5,
    "bergstrasse4_bezug": 1.1,
    "lindenweg7_allgemein": 0.6,
    "lindenweg7_whg1": 1.2,
    "lindenweg7_whg2": 0.9,
    # Deliberately smaller than the five consumption meters combined, so
    # the Verwaltung ends up owing money: its bill then also shows the
    # QR payment slip, which a credit note omits entirely.
    "lindenweg7_pv": 2.0,
    "parkstrasse12_allgemein": 0.5,
    "parkstrasse12_whg_eg": 1.4,
}


def _create_demo_readings(connection: sqlite3.Connection, metering_points: dict[str, MeteringPoint]) -> int:
    """Generate and store synthetic readings for the winter and summer quarters."""
    total = 0
    for handle, metering_point in metering_points.items():
        scale = _METERING_POINT_SCALES[handle]

        winter_readings = _generate_readings_for_quarter(
            metering_point_id=metering_point.id,
            direction=metering_point.direction,
            scale=scale,
            year=WINTER_QUARTER[0],
            quarter=WINTER_QUARTER[1],
            feed_in_disabled=True,
        )
        summer_readings = _generate_readings_for_quarter(
            metering_point_id=metering_point.id,
            direction=metering_point.direction,
            scale=scale,
            year=SUMMER_QUARTER[0],
            quarter=SUMMER_QUARTER[1],
            feed_in_disabled=False,
        )
        total += upsert_readings(connection, winter_readings)
        total += upsert_readings(connection, summer_readings)
    return total


def _set_demo_leg_settings(connection: sqlite3.Connection) -> None:
    """Fill in plausible sender data, a valid demo QR-IBAN and demo fees."""
    settings = settings_repo.get_settings(connection)
    settings.address_street = "Sonnenweg"
    settings.address_house_number = "10"
    settings.address_zip = "3000"
    settings.address_city = "Bern"
    settings.address_country = "CH"
    settings.qr_iban = _DEMO_QR_IBAN
    settings.admin_fee_consumption_rp_per_kwh = _DEMO_ADMIN_FEE_CONSUMPTION_RP_PER_KWH
    settings.admin_fee_feed_in_rp_per_kwh = _DEMO_ADMIN_FEE_FEED_IN_RP_PER_KWH
    settings.paper_invoice_rappen = _DEMO_PAPER_INVOICE_RAPPEN
    settings_repo.update_settings(connection, settings)
