"""Generates demo/test data: one LEG, four Standorte, seven Messpunkte,
five Personen (including a mid-quarter move), and synthetic 15-minute
readings for one winter and one summer quarter.

Used both to let the administrator click through the app with realistic
data, and as the fixture basis for the distribution-engine unit tests (see
`tests/test_distribution.py`), per the project brief's edge-case list:

- Winter quarter: no local Einspeisung at all (`P(t) = 0` throughout).
- Summer quarter: Einspeisung sometimes exceeds Bezug (`S(t) =
  min(P, C) = C`, testing the consumption-limited case) and sometimes falls
  short of it (testing the production-limited case).
- A Messpunkt that changes Person mid-quarter (tenant move), exercising
  the time-sliced Zuordnung lookup, while its Standort (and thus LEG)
  never changes.
"""

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.period import INTERVAL_MINUTES, quarter_bounds
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models import standort as standort_repo
from app.models import zuordnung as zuordnung_repo
from app.models.leg import Leg
from app.models.messpunkt import MESSRICHTUNG_BEZUG, MESSRICHTUNG_EINSPEISUNG, Messpunkt
from app.models.person import Person
from app.models.reading import Reading, upsert_readings
from app.models.standort import Standort
from app.models.zuordnung import Zuordnung

#: Demo QR-IBAN (valid checksum, QR-IID range) so generated demo data can
#: be used to produce QR-invoices end to end without manual configuration.
_DEMO_QR_IBAN = "CH5730000123456789012"

#: Demo admin surcharge and paper-invoice fee, matching realistic
#: real-world magnitudes (see `app.domain.billing`).
_DEMO_VERWALTUNGSAUFWAND_RP_PER_KWH = 0.5
_DEMO_PAPIERRECHNUNG_RAPPEN = 200

#: Year used for the generated demo quarters. Chosen in the past so both
#: quarters are always complete, regardless of when the app is run.
DEMO_YEAR = 2025

#: (year, quarter) for the winter fixture: no local Einspeisung.
WINTER_QUARTER = (DEMO_YEAR, 4)

#: (year, quarter) for the summer fixture: Einspeisung sometimes exceeds,
#: sometimes falls short of, Bezug.
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
        person_ids: Database ids of the created Personen.
        messpunkt_ids: Database ids of the created Messpunkte.
        reading_count: Total number of reading rows inserted.
    """

    person_ids: list[int]
    messpunkt_ids: list[int]
    reading_count: int


class DemoDataAlreadyExists(Exception):
    """Raised when demo data generation is requested but already ran."""


def demo_data_exists(connection: sqlite3.Connection) -> bool:
    """Check whether the demo data set has already been created.

    Args:
        connection: Open SQLite connection.

    Returns:
        `True` if a person with the demo marker name exists.
    """
    row = connection.execute(
        "SELECT 1 FROM person WHERE name = ?", (_DEMO_MARKER_NAME,)
    ).fetchone()
    return row is not None


def _consumption_kwh(moment: datetime, scale: float) -> float:
    """Compute a synthetic Bezug value for one 15-minute interval.

    Args:
        moment: Interval start.
        scale: Per-Messpunkt scale factor (relative household size).

    Returns:
        Energy for the interval in kWh, always positive.
    """
    kw = _HOURLY_LOAD_KW[moment.hour] * _WEEKDAY_FACTOR[moment.weekday()] * scale
    return round(kw * (INTERVAL_MINUTES / 60), 3)


def _production_kwh(moment: datetime, scale: float, day_index: int) -> float:
    """Compute a synthetic Einspeisung value for one 15-minute interval.

    Follows a bell curve between 06:00 and 20:00, zero outside daylight
    hours, scaled per day by `_SOLAR_DAY_SCALE` cycling through
    cloudy/mixed/sunny days so both surplus and deficit occur.

    Args:
        moment: Interval start.
        scale: Per-Messpunkt scale factor (relative installation size).
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
    messpunkt_id: int,
    messrichtung: str,
    scale: float,
    year: int,
    quarter: int,
    einspeisung_disabled: bool,
) -> list[Reading]:
    """Generate one quarter's worth of 15-minute synthetic readings.

    Args:
        messpunkt_id: Database id of the Messpunkt to generate readings for.
        messrichtung: The Messpunkt's `messrichtung`, determining Bezug vs.
            Einspeisung shape.
        scale: Per-Messpunkt scale factor.
        year: Calendar year of the quarter.
        quarter: Quarter number, 1 to 4.
        einspeisung_disabled: If `True`, Einspeisung-Messpunkte yield
            all-zero readings (used for the winter fixture).

    Returns:
        One `Reading` per 15-minute interval in the quarter.
    """
    start, end = quarter_bounds(year, quarter)
    is_einspeisung = messrichtung == MESSRICHTUNG_EINSPEISUNG

    readings: list[Reading] = []
    moment = start
    while moment < end:
        day_index = (moment.date() - start.date()).days
        if is_einspeisung:
            kwh = 0.0 if einspeisung_disabled else _production_kwh(moment, scale, day_index)
        else:
            kwh = _consumption_kwh(moment, scale)
        readings.append(
            Reading(
                messpunkt_id=messpunkt_id,
                timestamp=moment.isoformat(),
                direction=messrichtung,
                kwh=kwh,
                source="demo",
                import_batch_id=None,
            )
        )
        moment += timedelta(minutes=INTERVAL_MINUTES)
    return readings


def create_demo_data(connection: sqlite3.Connection) -> DemoDataSummary:
    """Create the full demo data set: LEG, Standorte, Messpunkte,
    Personen, Zuordnungen, readings.

    Idempotent guard: raises `DemoDataAlreadyExists` if the marker person
    is already present, so the button in the UI can be clicked safely
    without creating duplicates.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `DemoDataSummary` describing what was created.

    Raises:
        DemoDataAlreadyExists: If demo data was already generated before.
    """
    if demo_data_exists(connection):
        raise DemoDataAlreadyExists(
            "Demo-Daten wurden bereits erzeugt (Person "
            f'"{_DEMO_MARKER_NAME}" existiert schon).'
        )

    leg = _create_demo_leg(connection)
    standorte = _create_demo_standorte(connection, leg)
    messpunkte = _create_demo_messpunkte(connection, standorte)
    personen = _create_demo_personen(connection)
    _create_demo_zuordnungen(connection, personen, messpunkte)
    reading_count = _create_demo_readings(connection, messpunkte)
    _set_demo_leg_settings(connection)

    return DemoDataSummary(
        person_ids=[p.id for p in personen.values()],
        messpunkt_ids=[mp.id for mp in messpunkte.values()],
        reading_count=reading_count,
    )


def _create_demo_leg(connection: sqlite3.Connection) -> Leg:
    """Insert the single demo LEG all demo Standorte share.

    Args:
        connection: Open SQLite connection.

    Returns:
        The persisted `Leg` (with `id` set).
    """
    leg = Leg(
        id=None,
        name="Bern_TRA00001",
        gemeinde="Bern",
        bemerkung="",
        created_at="",
    )
    leg.id = leg_repo.create(connection, leg)
    return leg


def _create_demo_standorte(
    connection: sqlite3.Connection, leg: Leg
) -> dict[str, Standort]:
    """Insert the four demo Standorte, all on the demo LEG.

    Args:
        connection: Open SQLite connection.
        leg: LEG created by `_create_demo_leg`.

    Returns:
        A dict keyed by short handle ("anna", "beat", "carla",
        "bergstrasse4") mapping to the persisted `Standort` (with `id` set).
    """
    definitions = {
        "anna": Standort(
            id=None, adresse="Sonnenweg", hausnummer="1", plz="3000", gemeinde="Bern", lage="",
            leg_id=leg.id, netzebene="NE7", created_at="",
        ),
        "beat": Standort(
            id=None, adresse="Sonnenweg", hausnummer="2", plz="3000", gemeinde="Bern", lage="",
            leg_id=leg.id, netzebene="NE7", created_at="",
        ),
        "carla": Standort(
            id=None, adresse="Bergstrasse", hausnummer="3", plz="3001", gemeinde="Bern", lage="",
            leg_id=leg.id, netzebene="NE7", created_at="",
        ),
        "bergstrasse4": Standort(
            id=None, adresse="Bergstrasse", hausnummer="4", plz="3001", gemeinde="Bern", lage="",
            leg_id=leg.id, netzebene="NE7", created_at="",
        ),
    }
    created = {}
    for handle, standort in definitions.items():
        standort.id = standort_repo.create(connection, standort)
        created[handle] = standort
    return created


def _create_demo_messpunkte(
    connection: sqlite3.Connection, standorte: dict[str, Standort]
) -> dict[str, Messpunkt]:
    """Insert the demo Messpunkte for the showcase Standorte.

    Args:
        connection: Open SQLite connection.
        standorte: Standorte created by `_create_demo_standorte`.

    Returns:
        A dict keyed by short handle ("anna_bezug", "anna_einspeisung",
        "beat_bezug", "beat_einspeisung", "carla_bezug_1", "carla_bezug_2",
        "bergstrasse4_bezug") mapping to the persisted `Messpunkt` (with
        `id` set).
    """
    definitions = {
        "anna_bezug": Messpunkt(
            id=None, messpunkt_bezeichnung="CH1000000000000000000000001",
            messrichtung=MESSRICHTUNG_BEZUG, standort_id=standorte["anna"].id, created_at="",
        ),
        "anna_einspeisung": Messpunkt(
            id=None, messpunkt_bezeichnung="CH1000000000000000000000002",
            messrichtung=MESSRICHTUNG_EINSPEISUNG, standort_id=standorte["anna"].id, created_at="",
        ),
        "beat_bezug": Messpunkt(
            id=None, messpunkt_bezeichnung="CH1000000000000000000000003",
            messrichtung=MESSRICHTUNG_BEZUG, standort_id=standorte["beat"].id, created_at="",
        ),
        "beat_einspeisung": Messpunkt(
            id=None, messpunkt_bezeichnung="CH1000000000000000000000004",
            messrichtung=MESSRICHTUNG_EINSPEISUNG, standort_id=standorte["beat"].id, created_at="",
        ),
        "carla_bezug_1": Messpunkt(
            id=None, messpunkt_bezeichnung="CH1000000000000000000000005",
            messrichtung=MESSRICHTUNG_BEZUG, standort_id=standorte["carla"].id, created_at="",
        ),
        "carla_bezug_2": Messpunkt(
            id=None, messpunkt_bezeichnung="CH1000000000000000000000006",
            messrichtung=MESSRICHTUNG_BEZUG, standort_id=standorte["carla"].id, created_at="",
        ),
        "bergstrasse4_bezug": Messpunkt(
            id=None, messpunkt_bezeichnung="CH1000000000000000000000007",
            messrichtung=MESSRICHTUNG_BEZUG, standort_id=standorte["bergstrasse4"].id, created_at="",
        ),
    }
    created = {}
    for handle, messpunkt in definitions.items():
        messpunkt.id = messpunkt_repo.create(connection, messpunkt)
        created[handle] = messpunkt
    return created


def _create_demo_personen(connection: sqlite3.Connection) -> dict[str, Person]:
    """Insert the four showcase Personen plus one "previous tenant".

    Args:
        connection: Open SQLite connection.

    Returns:
        A dict keyed by short handle ("anna", "beat", "carla", "david",
        "erika") mapping to the persisted `Person` (with `id` set).
    """
    definitions = {
        "anna": Person(
            id=None, anrede="Frau", name=_DEMO_MARKER_NAME,
            kontakt_email="anna.muster@example.ch", kontakt_telefon="",
            rechnungsadresse_strasse="Sonnenweg 1", rechnungsadresse_plz="3000",
            rechnungsadresse_ort="Bern", rechnungsadresse_land="CH",
            iban="CH9300762011623852957", kundennummer=None, papierrechnung=False,
            created_at="",
        ),
        "beat": Person(
            id=None, anrede="Herr", name="Beat Beispiel (Demo)",
            kontakt_email="beat.beispiel@example.ch", kontakt_telefon="",
            rechnungsadresse_strasse="Sonnenweg 2", rechnungsadresse_plz="3000",
            rechnungsadresse_ort="Bern", rechnungsadresse_land="CH",
            iban="CH5604835012345678009", kundennummer=None, papierrechnung=True,
            created_at="",
        ),
        "carla": Person(
            id=None, anrede="Frau", name="Carla Consumer (Demo)",
            kontakt_email="carla.consumer@example.ch", kontakt_telefon="",
            rechnungsadresse_strasse="Bergstrasse 3", rechnungsadresse_plz="3001",
            rechnungsadresse_ort="Bern", rechnungsadresse_land="CH",
            iban="", kundennummer=None, papierrechnung=False,
            created_at="",
        ),
        "david": Person(
            id=None, anrede="Herr", name="David Demo (Demo)",
            kontakt_email="david.demo@example.ch", kontakt_telefon="",
            rechnungsadresse_strasse="Bergstrasse 4", rechnungsadresse_plz="3001",
            rechnungsadresse_ort="Bern", rechnungsadresse_land="CH",
            iban="", kundennummer=None, papierrechnung=False,
            created_at="",
        ),
        "erika": Person(
            id=None, anrede="Frau", name="Erika Vorgängerin (Demo, Umzug-Beispiel)",
            kontakt_email="", kontakt_telefon="",
            rechnungsadresse_strasse="Bergstrasse 4", rechnungsadresse_plz="3001",
            rechnungsadresse_ort="Bern", rechnungsadresse_land="CH",
            iban="", kundennummer=None, papierrechnung=False,
            created_at="",
        ),
    }
    created = {}
    for handle, person in definitions.items():
        person.id = person_repo.create(connection, person)
        created[handle] = person
    return created


def _create_demo_zuordnungen(
    connection: sqlite3.Connection,
    personen: dict[str, Person],
    messpunkte: dict[str, Messpunkt],
) -> None:
    """Insert Zuordnungen, including the mid-quarter move example.

    The "bergstrasse4_bezug" Messpunkt is assigned to Erika (previous
    tenant) until 2025-08-15 and to David from 2025-08-16 onward, so a
    single Messpunkt's readings are split between two Personen within the
    summer demo quarter -- while its Standort (Bergstrasse 4) and that
    Standort's LEG never change.

    Args:
        connection: Open SQLite connection.
        personen: Personen created by `_create_demo_personen`.
        messpunkte: Messpunkte created by `_create_demo_messpunkte`.

    Returns:
        None.
    """
    summer_start, _ = quarter_bounds(*SUMMER_QUARTER)
    move_date = date(2025, 8, 16)

    static_zuordnungen = [
        ("anna_bezug", "anna"),
        ("anna_einspeisung", "anna"),
        ("beat_bezug", "beat"),
        ("beat_einspeisung", "beat"),
        ("carla_bezug_1", "carla"),
        ("carla_bezug_2", "carla"),
    ]
    for messpunkt_handle, person_handle in static_zuordnungen:
        zuordnung_repo.create(
            connection,
            Zuordnung(
                id=None,
                person_id=personen[person_handle].id,
                messpunkt_id=messpunkte[messpunkt_handle].id,
                gueltig_von=summer_start.date(),
                gueltig_bis=None,
                created_at="",
            ),
        )

    # The move: Erika until the day before the move, David from the move on.
    zuordnung_repo.create(
        connection,
        Zuordnung(
            id=None,
            person_id=personen["erika"].id,
            messpunkt_id=messpunkte["bergstrasse4_bezug"].id,
            gueltig_von=summer_start.date(),
            gueltig_bis=move_date - timedelta(days=1),
            created_at="",
        ),
    )
    zuordnung_repo.create(
        connection,
        Zuordnung(
            id=None,
            person_id=personen["david"].id,
            messpunkt_id=messpunkte["bergstrasse4_bezug"].id,
            gueltig_von=move_date,
            gueltig_bis=None,
            created_at="",
        ),
    )


#: Per-Messpunkt scale factors used for both Bezug and Einspeisung shape.
_MESSPUNKT_SCALES = {
    "anna_bezug": 1.0,
    "anna_einspeisung": 4.0,
    "beat_bezug": 1.3,
    "beat_einspeisung": 3.0,
    "carla_bezug_1": 0.8,
    "carla_bezug_2": 1.5,
    "bergstrasse4_bezug": 1.1,
}


def _create_demo_readings(
    connection: sqlite3.Connection, messpunkte: dict[str, Messpunkt]
) -> int:
    """Generate and store synthetic readings for the winter and summer quarters.

    Args:
        connection: Open SQLite connection.
        messpunkte: Messpunkte created by `_create_demo_messpunkte`.

    Returns:
        The total number of reading rows inserted.
    """
    total = 0
    for handle, messpunkt in messpunkte.items():
        scale = _MESSPUNKT_SCALES[handle]

        winter_readings = _generate_readings_for_quarter(
            messpunkt_id=messpunkt.id,
            messrichtung=messpunkt.messrichtung,
            scale=scale,
            year=WINTER_QUARTER[0],
            quarter=WINTER_QUARTER[1],
            einspeisung_disabled=True,
        )
        summer_readings = _generate_readings_for_quarter(
            messpunkt_id=messpunkt.id,
            messrichtung=messpunkt.messrichtung,
            scale=scale,
            year=SUMMER_QUARTER[0],
            quarter=SUMMER_QUARTER[1],
            einspeisung_disabled=False,
        )
        total += upsert_readings(connection, winter_readings)
        total += upsert_readings(connection, summer_readings)
    return total


def _set_demo_leg_settings(connection: sqlite3.Connection) -> None:
    """Fill in plausible sender data, a valid demo QR-IBAN and demo fees.

    Lets the administrator generate real QR-invoice PDFs from the demo
    data without first having to configure real settings. Only ever
    called as part of `create_demo_data`, which itself only runs once on a
    fresh database (see `demo_data_exists`). The LEG's own name is set
    separately, on the `Leg` record itself (see `_create_demo_leg`).

    Args:
        connection: Open SQLite connection.

    Returns:
        None.
    """
    settings = settings_repo.get_settings(connection)
    settings.address_street = "Sonnenweg 10"
    settings.address_zip = "3000"
    settings.address_city = "Bern"
    settings.address_country = "CH"
    settings.qr_iban = _DEMO_QR_IBAN
    settings.verwaltungsaufwand_rp_per_kwh = _DEMO_VERWALTUNGSAUFWAND_RP_PER_KWH
    settings.papierrechnung_rappen = _DEMO_PAPIERRECHNUNG_RAPPEN
    settings_repo.update_settings(connection, settings)
