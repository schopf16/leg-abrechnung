"""Generates demo/test data: four example participants with meters, a
mid-quarter move, and synthetic 15-minute readings for one winter and one
summer quarter.

Used both to let the administrator click through the app with realistic
data, and as the fixture basis for the distribution-engine unit tests (see
`tests/test_distribution.py`), per the project brief's edge-case list:

- Winter quarter: no local production at all (`P(t) = 0` throughout).
- Summer quarter: production sometimes exceeds consumption (`S(t) =
  min(P, C) = C`, testing the consumption-limited case) and sometimes falls
  short of it (testing the production-limited case).
- A meter that changes participant mid-quarter (tenant move), exercising
  the time-sliced assignment lookup.
"""

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.period import INTERVAL_MINUTES, quarter_bounds
from app.models import assignment as assignment_repo
from app.models import meter as meter_repo
from app.models import participant as participant_repo
from app.models.assignment import MeterAssignment
from app.models.meter import Meter
from app.models.participant import Participant
from app.models.reading import Reading, upsert_readings

#: Year used for the generated demo quarters. Chosen in the past so both
#: quarters are always complete, regardless of when the app is run.
DEMO_YEAR = 2025

#: (year, quarter) for the winter fixture: no local solar production.
WINTER_QUARTER = (DEMO_YEAR, 4)

#: (year, quarter) for the summer fixture: production sometimes exceeds,
#: sometimes falls short of, consumption.
SUMMER_QUARTER = (DEMO_YEAR, 3)

#: Marker used to detect "demo data already created" and to keep the
#: generator idempotent.
_DEMO_MARKER_NAME = "Anna Muster (Demo)"

#: Typical household load shape, average kW per hour-of-day (index 0-23).
_HOURLY_LOAD_KW = [
    0.30, 0.25, 0.20, 0.20, 0.20, 0.25, 0.40, 0.70,
    0.60, 0.50, 0.45, 0.45, 0.50, 0.45, 0.40, 0.45,
    0.55, 0.75, 0.90, 0.85, 0.70, 0.55, 0.45, 0.35,
]

#: Weekly multiplier (Mon=0 .. Sun=6) giving slightly higher weekend use.
_WEEKDAY_FACTOR = [1.0, 1.0, 1.0, 1.0, 1.05, 1.2, 1.15]

#: Per-day-of-quarter solar scale cycling through cloudy/mixed/sunny days,
#: chosen so that both `P(t) > C(t)` and `P(t) < C(t)` occur in summer.
_SOLAR_DAY_SCALE = [0.5, 1.0, 1.4]


@dataclass
class DemoDataSummary:
    """Result of a successful demo data generation run.

    Attributes:
        participant_ids: Database ids of the created participants.
        meter_ids: Database ids of the created meters.
        reading_count: Total number of reading rows inserted.
    """

    participant_ids: list[int]
    meter_ids: list[int]
    reading_count: int


class DemoDataAlreadyExists(Exception):
    """Raised when demo data generation is requested but already ran."""


def demo_data_exists(connection: sqlite3.Connection) -> bool:
    """Check whether the demo data set has already been created.

    Args:
        connection: Open SQLite connection.

    Returns:
        `True` if a participant with the demo marker name exists.
    """
    row = connection.execute(
        "SELECT 1 FROM participants WHERE name = ?", (_DEMO_MARKER_NAME,)
    ).fetchone()
    return row is not None


def _consumption_kwh(moment: datetime, scale: float) -> float:
    """Compute a synthetic consumption value for one 15-minute interval.

    Args:
        moment: Interval start.
        scale: Per-meter scale factor (relative household size).

    Returns:
        Energy for the interval in kWh, always positive.
    """
    kw = _HOURLY_LOAD_KW[moment.hour] * _WEEKDAY_FACTOR[moment.weekday()] * scale
    return round(kw * (INTERVAL_MINUTES / 60), 3)


def _production_kwh(moment: datetime, scale: float, day_index: int) -> float:
    """Compute a synthetic solar production value for one 15-minute interval.

    Follows a bell curve between 06:00 and 20:00, zero outside daylight
    hours, scaled per day by :data:`_SOLAR_DAY_SCALE` cycling through
    cloudy/mixed/sunny days so both production surplus and deficit occur.

    Args:
        moment: Interval start.
        scale: Per-meter scale factor (relative installation size).
        day_index: Zero-based day offset since the start of the quarter,
            used to pick the day's weather scale.

    Returns:
        Energy for the interval in kWh, zero outside daylight hours.
    """
    hour = moment.hour + moment.minute / 60
    if hour < 6 or hour > 20:
        return 0.0
    bell = math.sin(math.pi * (hour - 6) / 14)
    day_scale = _SOLAR_DAY_SCALE[day_index % len(_SOLAR_DAY_SCALE)]
    kw = bell * scale * day_scale
    return round(max(kw, 0.0) * (INTERVAL_MINUTES / 60), 3)


def _generate_readings_for_quarter(
    meter_id: int,
    role: str,
    scale: float,
    year: int,
    quarter: int,
    production_disabled: bool,
) -> list[Reading]:
    """Generate one quarter's worth of 15-minute synthetic readings for a meter.

    Args:
        meter_id: Database id of the meter to generate readings for.
        role: The meter's role, determining consumption vs. production shape.
        scale: Per-meter scale factor.
        year: Calendar year of the quarter.
        quarter: Quarter number, 1 to 4.
        production_disabled: If `True`, production meters yield all-zero
            readings (used for the winter fixture).

    Returns:
        One `Reading` per 15-minute interval in the quarter.
    """
    start, end = quarter_bounds(year, quarter)
    is_production = role == "produktion"
    direction = "produktion" if is_production else "bezug"

    readings: list[Reading] = []
    moment = start
    while moment < end:
        day_index = (moment.date() - start.date()).days
        if is_production:
            kwh = 0.0 if production_disabled else _production_kwh(moment, scale, day_index)
        else:
            kwh = _consumption_kwh(moment, scale)
        readings.append(
            Reading(
                meter_id=meter_id,
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
    """Create the full demo data set: participants, meters, assignments, readings.

    Idempotent guard: raises `DemoDataAlreadyExists` if the marker
    participant is already present, so the button in the UI can be clicked
    safely without creating duplicates.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `DemoDataSummary` describing what was created.

    Raises:
        DemoDataAlreadyExists: If demo data was already generated before.
    """
    if demo_data_exists(connection):
        raise DemoDataAlreadyExists(
            "Demo-Daten wurden bereits erzeugt (Teilnehmer "
            f'"{_DEMO_MARKER_NAME}" existiert schon).'
        )

    participants = _create_demo_participants(connection)
    meters = _create_demo_meters(connection)
    _create_demo_assignments(connection, participants, meters)
    reading_count = _create_demo_readings(connection, meters)

    return DemoDataSummary(
        participant_ids=[p.id for p in participants.values()],
        meter_ids=[m.id for m in meters.values()],
        reading_count=reading_count,
    )


def _create_demo_participants(
    connection: sqlite3.Connection,
) -> dict[str, Participant]:
    """Insert the four showcase participants plus one "previous tenant".

    Args:
        connection: Open SQLite connection.

    Returns:
        A dict keyed by short handle ("anna", "beat", "carla", "david",
        "erika") mapping to the persisted `Participant` (with `id` set).
    """
    definitions = {
        "anna": Participant(
            id=None,
            name=_DEMO_MARKER_NAME,
            address_street="Sonnenweg 1",
            address_zip="3000",
            address_city="Bern",
            address_country="CH",
            iban="CH9300762011623852957",
            email="anna.muster@example.ch",
            created_at="",
        ),
        "beat": Participant(
            id=None,
            name="Beat Beispiel (Demo)",
            address_street="Sonnenweg 2",
            address_zip="3000",
            address_city="Bern",
            address_country="CH",
            iban="CH5604835012345678009",
            email="beat.beispiel@example.ch",
            created_at="",
        ),
        "carla": Participant(
            id=None,
            name="Carla Consumer (Demo)",
            address_street="Bergstrasse 3",
            address_zip="3001",
            address_city="Bern",
            address_country="CH",
            iban="",
            email="carla.consumer@example.ch",
            created_at="",
        ),
        "david": Participant(
            id=None,
            name="David Demo (Demo)",
            address_street="Bergstrasse 4",
            address_zip="3001",
            address_city="Bern",
            address_country="CH",
            iban="",
            email="david.demo@example.ch",
            created_at="",
        ),
        "erika": Participant(
            id=None,
            name="Erika Vorgängerin (Demo, Umzug-Beispiel)",
            address_street="Bergstrasse 4",
            address_zip="3001",
            address_city="Bern",
            address_country="CH",
            iban="",
            email="",
            created_at="",
        ),
    }
    created = {}
    for handle, participant in definitions.items():
        participant.id = participant_repo.create(connection, participant)
        created[handle] = participant
    return created


def _create_demo_meters(connection: sqlite3.Connection) -> dict[str, Meter]:
    """Insert the demo meters for the showcase participants.

    Args:
        connection: Open SQLite connection.

    Returns:
        A dict keyed by short handle ("anna_bezug", "anna_prod",
        "beat_bezug", "beat_prod", "carla_fix", "carla_wp", "david_bezug")
        mapping to the persisted `Meter` (with `id` set).
    """
    definitions = {
        "anna_bezug": Meter(
            id=None,
            metering_point_id="CH1000000000000000000000001",
            label="Anna Muster - Bezug",
            building_address="Sonnenweg 1, 3000 Bern",
            role="bezug",
            created_at="",
        ),
        "anna_prod": Meter(
            id=None,
            metering_point_id="CH1000000000000000000000002",
            label="Anna Muster - Produktion (PV)",
            building_address="Sonnenweg 1, 3000 Bern",
            role="produktion",
            created_at="",
        ),
        "beat_bezug": Meter(
            id=None,
            metering_point_id="CH1000000000000000000000003",
            label="Beat Beispiel - Bezug",
            building_address="Sonnenweg 2, 3000 Bern",
            role="bezug",
            created_at="",
        ),
        "beat_prod": Meter(
            id=None,
            metering_point_id="CH1000000000000000000000004",
            label="Beat Beispiel - Produktion (PV)",
            building_address="Sonnenweg 2, 3000 Bern",
            role="produktion",
            created_at="",
        ),
        "carla_fix": Meter(
            id=None,
            metering_point_id="CH1000000000000000000000005",
            label="Carla Consumer - Bezug (fix)",
            building_address="Bergstrasse 3, 3001 Bern",
            role="bezug_fix",
            created_at="",
        ),
        "carla_wp": Meter(
            id=None,
            metering_point_id="CH1000000000000000000000006",
            label="Carla Consumer - Bezug (Wärmepumpe, geschaltet)",
            building_address="Bergstrasse 3, 3001 Bern",
            role="bezug_geschaltet",
            created_at="",
        ),
        "david_bezug": Meter(
            id=None,
            metering_point_id="CH1000000000000000000000007",
            label="Bergstrasse 4 - Bezug",
            building_address="Bergstrasse 4, 3001 Bern",
            role="bezug",
            created_at="",
        ),
    }
    created = {}
    for handle, meter in definitions.items():
        meter.id = meter_repo.create(connection, meter)
        created[handle] = meter
    return created


def _create_demo_assignments(
    connection: sqlite3.Connection,
    participants: dict[str, Participant],
    meters: dict[str, Meter],
) -> None:
    """Insert meter assignments, including the mid-quarter move example.

    The "Bergstrasse 4 - Bezug" meter is assigned to Erika (previous
    tenant) until 2025-08-15 and to David from 2025-08-16 onward, so a
    single meter's readings are split between two participants within the
    summer demo quarter.

    Args:
        connection: Open SQLite connection.
        participants: Participants created by `_create_demo_participants`.
        meters: Meters created by `_create_demo_meters`.

    Returns:
        None.
    """
    quarter_start, _ = quarter_bounds(*WINTER_QUARTER)
    summer_start, _ = quarter_bounds(*SUMMER_QUARTER)
    move_date = date(2025, 8, 16)

    static_assignments = [
        ("anna_bezug", "anna"),
        ("anna_prod", "anna"),
        ("beat_bezug", "beat"),
        ("beat_prod", "beat"),
        ("carla_fix", "carla"),
        ("carla_wp", "carla"),
    ]
    for meter_handle, participant_handle in static_assignments:
        assignment_repo.create(
            connection,
            MeterAssignment(
                id=None,
                meter_id=meters[meter_handle].id,
                participant_id=participants[participant_handle].id,
                valid_from=summer_start.date(),
                valid_to=None,
                created_at="",
            ),
        )

    # The move: Erika until the day before the move, David from the move on.
    assignment_repo.create(
        connection,
        MeterAssignment(
            id=None,
            meter_id=meters["david_bezug"].id,
            participant_id=participants["erika"].id,
            valid_from=summer_start.date(),
            valid_to=move_date - timedelta(days=1),
            created_at="",
        ),
    )
    assignment_repo.create(
        connection,
        MeterAssignment(
            id=None,
            meter_id=meters["david_bezug"].id,
            participant_id=participants["david"].id,
            valid_from=move_date,
            valid_to=None,
            created_at="",
        ),
    )


#: Per-meter scale factors used for both consumption and production shape.
_METER_SCALES = {
    "anna_bezug": 1.0,
    "anna_prod": 4.0,
    "beat_bezug": 1.3,
    "beat_prod": 3.0,
    "carla_fix": 0.8,
    "carla_wp": 1.5,
    "david_bezug": 1.1,
}


def _create_demo_readings(
    connection: sqlite3.Connection, meters: dict[str, Meter]
) -> int:
    """Generate and store synthetic readings for the winter and summer quarters.

    Args:
        connection: Open SQLite connection.
        meters: Meters created by `_create_demo_meters`.

    Returns:
        The total number of reading rows inserted.
    """
    total = 0
    for handle, meter in meters.items():
        scale = _METER_SCALES[handle]

        winter_readings = _generate_readings_for_quarter(
            meter_id=meter.id,
            role=meter.role,
            scale=scale,
            year=WINTER_QUARTER[0],
            quarter=WINTER_QUARTER[1],
            production_disabled=True,
        )
        summer_readings = _generate_readings_for_quarter(
            meter_id=meter.id,
            role=meter.role,
            scale=scale,
            year=SUMMER_QUARTER[0],
            quarter=SUMMER_QUARTER[1],
            production_disabled=False,
        )
        total += upsert_readings(connection, winter_readings)
        total += upsert_readings(connection, summer_readings)
    return total
