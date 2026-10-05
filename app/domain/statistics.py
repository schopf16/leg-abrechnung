"""Trend statistics for the Statistik page: energy flow and master-data growth over a trailing window
of calendar months."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from app.domain.period import (
    GRANULARITY_QUARTER_HOUR,
    INTERVAL_MINUTES,
    bucket_key,
    bucket_key_of,
    buckets_in,
    month_bounds,
    quarter_bounds,
    trailing_months,
)
from app.models import assignment as assignment_repo
from app.models import metering_point as metering_point_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN
from app.models.person_onboarding import STEPS as ONBOARDING_STEPS
from app.sort_keys import text_key


@dataclass
class MonthlyEnergy:
    """One calendar month's total local consumption and feed-in."""

    year: int
    month: int
    consumption_kwh: float
    feed_in_kwh: float

    @property
    def balance_kwh(self) -> float:
        """Feed-in minus consumption -- positive means a net surplus."""
        return self.feed_in_kwh - self.consumption_kwh


@dataclass
class MonthlyGrowth:
    """Cumulative master-data counts as of the end of one calendar month."""

    year: int
    month: int
    persons: int
    metering_points: int
    sites: int
    substation_areas: int
    legs: int


def monthly_energy_totals(
    connection: sqlite3.Connection,
    leg_id: Optional[int] = None,
    reference_date: Optional[date] = None,
    months: int = 12,
) -> list[MonthlyEnergy]:
    """Aggregate consumption/feed-in totals per month over a trailing window."""
    window = trailing_months(reference_date or date.today(), months)
    start = f"{window[0][0]:04d}-{window[0][1]:02d}-01"
    end_year, end_month = window[-1]
    end = f"{end_year + 1:04d}-01-01" if end_month == 12 else f"{end_year:04d}-{end_month + 1:02d}-01"

    query = """
        SELECT substr(r.timestamp, 1, 7) AS ym, r.direction, SUM(r.kwh) AS total
        FROM readings r
        JOIN metering_point mp ON mp.id = r.metering_point_id
        WHERE r.timestamp >= ? AND r.timestamp < ?
    """
    params: list = [start, end]
    if leg_id is not None:
        query += " AND mp.leg_id = ?"
        params.append(leg_id)
    query += " GROUP BY ym, r.direction"

    totals: dict[str, dict[str, float]] = {}
    for row in connection.execute(query, params):
        totals.setdefault(row["ym"], {"consumption": 0.0, "feed_in": 0.0})[row["direction"]] = row["total"]

    return [
        MonthlyEnergy(
            year=year,
            month=month,
            consumption_kwh=round(totals.get(f"{year:04d}-{month:02d}", {}).get("consumption", 0.0), 3),
            feed_in_kwh=round(totals.get(f"{year:04d}-{month:02d}", {}).get("feed_in", 0.0), 3),
        )
        for year, month in window
    ]


def _creation_dates(connection: sqlite3.Connection, table: str) -> list[date]:
    """Fetch a table's `created_at` timestamps as plain dates."""
    # nosec B608 -- `table` is one of the fixed table names above, never user input
    rows = connection.execute(f"SELECT created_at FROM {table}").fetchall()  # nosec B608
    return [datetime.fromisoformat(row["created_at"]).date() for row in rows if row["created_at"]]


def monthly_growth_counts(
    connection: sqlite3.Connection,
    reference_date: Optional[date] = None,
    months: int = 12,
) -> list[MonthlyGrowth]:
    """Compute cumulative master-data counts per month over a trailing window."""
    window = trailing_months(reference_date or date.today(), months)

    persons = _creation_dates(connection, "person")
    metering_points = _creation_dates(connection, "metering_point")
    sites = _creation_dates(connection, "site")
    substation_areas = _creation_dates(connection, "substation_area")
    legs = _creation_dates(connection, "leg")

    results = []
    for year, month in window:
        _, last_day = month_bounds(year, month)
        results.append(
            MonthlyGrowth(
                year=year,
                month=month,
                persons=sum(1 for d in persons if d <= last_day),
                metering_points=sum(1 for d in metering_points if d <= last_day),
                sites=sum(1 for d in sites if d <= last_day),
                substation_areas=sum(1 for d in substation_areas if d <= last_day),
                legs=sum(1 for d in legs if d <= last_day),
            )
        )
    return results


@dataclass
class QuarterEnergy:
    """What a quarter's imported data actually contains, for one LEG or all."""

    year: int
    quarter: int
    consumption_kwh: float
    feed_in_kwh: float
    reading_count: int
    metering_points_with_readings: int
    metering_points_expected: int
    last_import_at: Optional[str]
    import_sources: list[str]

    @property
    def can_share(self) -> bool:
        """Whether local sharing is possible at all in this quarter."""
        return self.consumption_kwh > 0 and self.feed_in_kwh > 0

    @property
    def missing_metering_points(self) -> int:
        """Metering points that were assigned but delivered no readings."""
        return max(0, self.metering_points_expected - self.metering_points_with_readings)

    @property
    def note(self) -> Optional[str]:
        """A German warning about this quarter, or `None` if it looks sound."""
        if self.reading_count == 0:
            return "Keine Messdaten in diesem Quartal."
        if self.feed_in_kwh <= 0:
            return "Keine Einspeisung -- lokale Verteilung nicht möglich, alle Positionen wären 0 kWh."
        if self.consumption_kwh <= 0:
            return "Kein Bezug -- lokale Verteilung nicht möglich, alle Positionen wären 0 kWh."
        if self.missing_metering_points:
            return (
                f"{self.missing_metering_points} zugeordnete Messpunkte ohne Messdaten -- "
                "Import möglicherweise unvollständig."
            )
        return None


def quarter_energy_totals(
    connection: sqlite3.Connection, leg_id: Optional[int] = None
) -> list[QuarterEnergy]:
    """Summarise every quarter that has readings, newest first."""
    # `? IS NULL OR ...` rather than appending a filter clause: the query
    # stays one fixed string, so there is no SQL assembled from Python
    # values anywhere in this module.
    rows = connection.execute(
        """
        SELECT substr(r.timestamp, 1, 4) AS yr,
               (CAST(substr(r.timestamp, 6, 2) AS INTEGER) - 1) / 3 + 1 AS qr,
               r.direction,
               SUM(r.kwh) AS total,
               COUNT(*) AS rows_count,
               MAX(b.imported_at) AS last_import,
               GROUP_CONCAT(DISTINCT r.source) AS sources
        FROM readings r
        JOIN metering_point mp ON mp.id = r.metering_point_id
        LEFT JOIN import_batches b ON b.id = r.import_batch_id
        WHERE (? IS NULL OR mp.leg_id = ?)
        GROUP BY yr, qr, r.direction
        """,
        (leg_id, leg_id),
    ).fetchall()

    grouped: dict[tuple[int, int], dict] = {}
    for row in rows:
        key = (int(row["yr"]), int(row["qr"]))
        entry = grouped.setdefault(
            key,
            {"consumption": 0.0, "feed_in": 0.0, "rows": 0, "last_import": None, "sources": set()},
        )
        entry[row["direction"]] = row["total"] or 0.0
        entry["rows"] += row["rows_count"]
        if row["last_import"] and (entry["last_import"] is None or row["last_import"] > entry["last_import"]):
            entry["last_import"] = row["last_import"]
        if row["sources"]:
            entry["sources"].update(row["sources"].split(","))

    results = []
    for (year, quarter), entry in sorted(grouped.items(), reverse=True):
        start, end = quarter_bounds(year, quarter)
        # Counted on its own rather than in the query above: a metering
        # point measuring both directions would otherwise be counted once
        # per direction.
        distinct = connection.execute(
            """
            SELECT COUNT(DISTINCT r.metering_point_id) AS n
            FROM readings r
            JOIN metering_point mp ON mp.id = r.metering_point_id
            WHERE r.timestamp >= ? AND r.timestamp < ? AND (? IS NULL OR mp.leg_id = ?)
            """,
            (start.isoformat(), end.isoformat(), leg_id, leg_id),
        ).fetchone()
        results.append(
            QuarterEnergy(
                year=year,
                quarter=quarter,
                consumption_kwh=round(entry["consumption"], 3),
                feed_in_kwh=round(entry["feed_in"], 3),
                reading_count=entry["rows"],
                metering_points_with_readings=distinct["n"] or 0,
                metering_points_expected=_assigned_metering_point_count(connection, year, quarter, leg_id),
                last_import_at=entry["last_import"],
                import_sources=sorted(entry["sources"]),
            )
        )
    return results


def _assigned_metering_point_count(
    connection: sqlite3.Connection, year: int, quarter: int, leg_id: Optional[int]
) -> int:
    """Count metering points whose assignment overlaps a quarter."""
    start, end = quarter_bounds(year, quarter)
    last_day = (end.date() - timedelta(days=1)).isoformat()
    row = connection.execute(
        """
        SELECT COUNT(DISTINCT a.metering_point_id) AS n
        FROM assignment a
        JOIN metering_point mp ON mp.id = a.metering_point_id
        WHERE a.valid_from <= ? AND (a.valid_to IS NULL OR a.valid_to >= ?)
          AND (? IS NULL OR mp.leg_id = ?)
        """,
        (last_day, start.date().isoformat(), leg_id, leg_id),
    ).fetchone()
    return row["n"] or 0


#: Upper bound above which a hand-entered capacity is treated as a typo
#: rather than a value. Not a physical claim: the largest real figure in
#: this deployment is 25 kWp, and somebody entering 18.5 as 18500 must not
#: be able to triple the reported total. Applies to kWp and to kWh alike.
CAPACITY_PLAUSIBLE_MAX = 1000.0


@dataclass
class InstalledCapacity:
    """Installed PV power and battery capacity, summed over plausible values."""

    pv_kwp: float
    pv_counted: int
    pv_expected: int
    battery_kwh: float
    battery_counted: int
    implausible: list[str]


def _plausible_capacity(value: Optional[float]) -> bool:
    """Whether a hand-entered capacity can be summed."""
    return value is not None and 0 < value < CAPACITY_PLAUSIBLE_MAX


def installed_capacity_totals(
    connection: sqlite3.Connection, leg_id: Optional[int] = None
) -> InstalledCapacity:
    """Sum the installed PV power and battery capacity on record."""
    rows = connection.execute(
        """
        SELECT designation, direction, pv_capacity_kwp, battery_capacity_kwh
        FROM metering_point
        WHERE ? IS NULL OR leg_id = ?
        """,
        (leg_id, leg_id),
    ).fetchall()

    pv_kwp = 0.0
    pv_counted = 0
    pv_expected = 0
    battery_kwh = 0.0
    battery_counted = 0
    implausible: list[str] = []

    for row in rows:
        is_feed_in = row["direction"] == DIRECTION_FEED_IN
        if is_feed_in:
            pv_expected += 1

        pv_value = row["pv_capacity_kwp"]
        if pv_value is not None:
            if is_feed_in and _plausible_capacity(pv_value):
                pv_kwp += pv_value
                pv_counted += 1
            else:
                implausible.append(row["designation"])

        battery_value = row["battery_capacity_kwh"]
        if battery_value is not None:
            if _plausible_capacity(battery_value):
                battery_kwh += battery_value
                battery_counted += 1
            elif row["designation"] not in implausible:
                implausible.append(row["designation"])

    return InstalledCapacity(
        pv_kwp=round(pv_kwp, 2),
        pv_counted=pv_counted,
        pv_expected=pv_expected,
        battery_kwh=round(battery_kwh, 2),
        battery_counted=battery_counted,
        implausible=implausible,
    )


@dataclass
class EnergyBucket:
    """One point on the energy chart's x-axis."""

    start: datetime
    consumption_kwh: float
    feed_in_kwh: float
    shared_kwh: float

    @property
    def self_consumption_share(self) -> Optional[float]:
        """How much of the local production was used locally, 0 to 1."""
        if self.feed_in_kwh <= 0:
            return None
        return self.shared_kwh / self.feed_in_kwh

    @property
    def local_coverage_share(self) -> Optional[float]:
        """How much of the consumption was covered locally, 0 to 1."""
        if self.consumption_kwh <= 0:
            return None
        return self.shared_kwh / self.consumption_kwh

    def value_for(self, granularity_key: str) -> tuple[float, float, float]:
        """The three figures in the unit that resolution is drawn in."""
        values = (self.consumption_kwh, self.feed_in_kwh, self.shared_kwh)
        if granularity_key != GRANULARITY_QUARTER_HOUR:
            return values
        factor = 60 / INTERVAL_MINUTES
        return tuple(round(value * factor, 3) for value in values)


def energy_unit(granularity_key: str) -> str:
    """The unit the energy chart's y-axis carries at one resolution."""
    return "kW" if granularity_key == GRANULARITY_QUARTER_HOUR else "kWh"


def energy_series(
    connection: sqlite3.Connection,
    granularity_key: str,
    window: tuple[datetime, datetime],
    leg_id: Optional[int] = None,
) -> list[EnergyBucket]:
    """Consumption, feed-in and locally shared energy over one window."""
    start, end = window
    rows = connection.execute(
        """
        SELECT r.timestamp AS ts,
               mp.leg_id AS leg_id,
               SUM(CASE WHEN r.direction = ? THEN r.kwh ELSE 0 END) AS consumption,
               SUM(CASE WHEN r.direction <> ? THEN r.kwh ELSE 0 END) AS feed_in
        FROM readings r
        JOIN metering_point mp ON mp.id = r.metering_point_id
        WHERE r.timestamp >= ? AND r.timestamp < ?
          AND (? IS NULL OR mp.leg_id = ?)
        GROUP BY r.timestamp, mp.leg_id
        """,
        (
            DIRECTION_CONSUMPTION,
            DIRECTION_CONSUMPTION,
            start.isoformat(),
            end.isoformat(),
            leg_id,
            leg_id,
        ),
    ).fetchall()

    totals: dict[str, list[float]] = {}
    for row in rows:
        key = bucket_key(granularity_key, row["ts"])
        consumption = row["consumption"] or 0.0
        feed_in = row["feed_in"] or 0.0
        # min() per interval and per LEG, before anything is summed.
        shared = min(consumption, feed_in)
        bucket = totals.setdefault(key, [0.0, 0.0, 0.0])
        bucket[0] += consumption
        bucket[1] += feed_in
        bucket[2] += shared

    series = []
    for bucket_start in buckets_in(granularity_key, window):
        consumption, feed_in, shared = totals.get(
            bucket_key_of(granularity_key, bucket_start), (0.0, 0.0, 0.0)
        )
        series.append(
            EnergyBucket(
                start=bucket_start,
                consumption_kwh=round(consumption, 3),
                feed_in_kwh=round(feed_in, 3),
                shared_kwh=round(shared, 3),
            )
        )
    return series


@dataclass
class ReceivablesBucket:
    """One point on the receivables chart."""

    start: datetime
    invoiced_rappen: int
    received_rappen: int
    open_rappen: int

    @property
    def invoiced_chf(self) -> float:
        """`invoiced_rappen` in francs, for the chart's axis."""
        return round(self.invoiced_rappen / 100, 2)

    @property
    def received_chf(self) -> float:
        """`received_rappen` in francs."""
        return round(self.received_rappen / 100, 2)

    @property
    def open_chf(self) -> float:
        """`open_rappen` in francs."""
        return round(self.open_rappen / 100, 2)


def receivables_series(
    connection: sqlite3.Connection,
    granularity_key: str,
    window: tuple[datetime, datetime],
) -> list[ReceivablesBucket]:
    """Invoiced, received and still-open amounts over one window."""
    start, end = window

    invoiced_rows = connection.execute(
        """
        SELECT br.created_at AS at, SUM(i.net_amount_rappen) AS total
        FROM billing_run_items i
        JOIN billing_runs br ON br.id = i.billing_run_id
        WHERE br.created_at < ?
        GROUP BY br.created_at
        """,
        (end.isoformat(),),
    ).fetchall()
    received_rows = connection.execute(
        """
        SELECT booked_at AS at, SUM(amount_rappen) AS total
        FROM account_entries
        WHERE booked_at < ?
        GROUP BY booked_at
        """,
        (end.isoformat(),),
    ).fetchall()

    invoiced_by_bucket: dict[str, int] = {}
    received_by_bucket: dict[str, int] = {}
    # Everything that happened before the window still counts towards the
    # open amount the window starts at.
    # Both tables already carry the same sign convention -- an invoice is
    # positive, an incoming payment negative -- so the open amount is
    # simply their sum and nothing needs negating to compute it. The one
    # place a sign is flipped is `received_rappen` below, which reports
    # "money that arrived" and would otherwise read as a negative number
    # on a chart.
    carried = 0
    window_start = start.isoformat()
    for rows, per_bucket in ((invoiced_rows, invoiced_by_bucket), (received_rows, received_by_bucket)):
        for row in rows:
            amount = int(row["total"] or 0)
            if row["at"] < window_start:
                carried += amount
                continue
            key = bucket_key(granularity_key, row["at"])
            per_bucket[key] = per_bucket.get(key, 0) + amount

    series = []
    running = carried
    for bucket_start in buckets_in(granularity_key, window):
        key = bucket_key_of(granularity_key, bucket_start)
        invoiced = invoiced_by_bucket.get(key, 0)
        # Stored negative for an incoming payment, so what arrived is the
        # negation of the booked amount -- read, not rewritten.
        received = -received_by_bucket.get(key, 0)
        running += invoiced - received
        series.append(
            ReceivablesBucket(
                start=bucket_start,
                invoiced_rappen=invoiced,
                received_rappen=received,
                open_rappen=running,
            )
        )
    return series


@dataclass
class FunnelStep:
    """One step of the onboarding pipeline and how many people wait at it."""

    label: str
    waiting: int


def onboarding_funnel(connection: sqlite3.Connection) -> tuple[list[FunnelStep], int]:
    """Where the membership pipeline is stuck, step by step."""
    trackers = person_onboarding_repo.list_all(connection)
    waiting: dict[str, int] = {label: 0 for _, label in ONBOARDING_STEPS}
    completed = 0
    for tracker in trackers:
        if tracker.is_complete:
            completed += 1
            continue
        _, label = tracker.current_step
        waiting[label] = waiting.get(label, 0) + 1
    return [FunnelStep(label=label, waiting=waiting[label]) for _, label in ONBOARDING_STEPS], completed


@dataclass
class LegDistribution:
    """One LEG's share of the deployment."""

    leg_id: int
    name: str
    feed_in_metering_points: int
    consumption_metering_points: int
    pv_kwp: float

    @property
    def metering_points(self) -> int:
        """Both directions together."""
        return self.feed_in_metering_points + self.consumption_metering_points


def distribution_by_leg(connection: sqlite3.Connection) -> list[LegDistribution]:
    """How the metering points and the installed power sit across the LEGs."""
    rows = connection.execute(
        """
        SELECT l.id AS leg_id, l.name AS name,
               SUM(CASE WHEN mp.direction = ? THEN 1 ELSE 0 END) AS feed_in,
               SUM(CASE WHEN mp.direction = ? THEN 1 ELSE 0 END) AS consumption
        FROM leg l
        LEFT JOIN metering_point mp ON mp.leg_id = l.id
        GROUP BY l.id
        """,
        (DIRECTION_FEED_IN, DIRECTION_CONSUMPTION),
    ).fetchall()

    distributions = [
        LegDistribution(
            leg_id=row["leg_id"],
            name=row["name"],
            feed_in_metering_points=row["feed_in"] or 0,
            consumption_metering_points=row["consumption"] or 0,
            pv_kwp=installed_capacity_totals(connection, row["leg_id"]).pv_kwp,
        )
        for row in rows
    ]
    return sorted(distributions, key=lambda d: (-d.metering_points, text_key(d.name)))


@dataclass
class LegBalance:
    """How well one LEG's producing and consuming sides match each other."""

    leg_id: int
    name: str
    producer_metering_points: int
    consumer_metering_points: int
    consumption_kwh: float
    feed_in_kwh: float
    shared_kwh: float

    @property
    def metering_points(self) -> int:
        """Both directions together."""
        return self.producer_metering_points + self.consumer_metering_points

    @property
    def producers_per_consumer(self) -> Optional[float]:
        """Feed-in meters per consumption meter."""
        if self.consumer_metering_points == 0:
            return None
        return self.producer_metering_points / self.consumer_metering_points

    @property
    def one_sided_note(self) -> Optional[str]:
        """The German statement of a missing side, if one is missing."""
        if self.producer_metering_points and self.consumer_metering_points:
            return None
        if not self.metering_points:
            return None
        return "nur Konsumenten" if self.producer_metering_points == 0 else "nur Produzenten"

    @property
    def has_readings(self) -> bool:
        """Whether any reading was imported for this LEG."""
        return bool(self.consumption_kwh or self.feed_in_kwh)

    @property
    def shared_share_of_production(self) -> Optional[float]:
        """What percentage of the fed-in energy found a local taker."""
        if not self.feed_in_kwh:
            return None
        return self.shared_kwh / self.feed_in_kwh * 100

    @property
    def local_coverage(self) -> Optional[float]:
        """What percentage of the drawn energy came from inside the LEG."""
        if not self.consumption_kwh:
            return None
        return self.shared_kwh / self.consumption_kwh * 100


def shared_energy_by_leg(connection: sqlite3.Connection) -> dict[int, tuple[float, float, float]]:
    """Total consumption, feed-in and shared energy per LEG, over all readings."""
    rows = connection.execute(
        """
        SELECT mp.leg_id AS leg_id,
               r.timestamp AS ts,
               SUM(CASE WHEN r.direction = ? THEN r.kwh ELSE 0 END) AS consumption,
               SUM(CASE WHEN r.direction <> ? THEN r.kwh ELSE 0 END) AS feed_in
        FROM readings r
        JOIN metering_point mp ON mp.id = r.metering_point_id
        WHERE mp.leg_id IS NOT NULL
        GROUP BY mp.leg_id, r.timestamp
        """,
        (DIRECTION_CONSUMPTION, DIRECTION_CONSUMPTION),
    ).fetchall()

    totals: dict[int, list[float]] = {}
    for row in rows:
        consumption = row["consumption"] or 0.0
        feed_in = row["feed_in"] or 0.0
        bucket = totals.setdefault(row["leg_id"], [0.0, 0.0, 0.0])
        bucket[0] += consumption
        bucket[1] += feed_in
        # min() per interval and per LEG, before anything is summed.
        bucket[2] += min(consumption, feed_in)
    return {leg_id: (values[0], values[1], values[2]) for leg_id, values in totals.items()}


def leg_balance(connection: sqlite3.Connection) -> list[LegBalance]:
    """How the LEGs compare, from production-heavy to consumption-heavy."""
    energy = shared_energy_by_leg(connection)
    balances = []
    for distribution in distribution_by_leg(connection):
        consumption_kwh, feed_in_kwh, shared_kwh = energy.get(distribution.leg_id, (0.0, 0.0, 0.0))
        balances.append(
            LegBalance(
                leg_id=distribution.leg_id,
                name=distribution.name,
                producer_metering_points=distribution.feed_in_metering_points,
                consumer_metering_points=distribution.consumption_metering_points,
                consumption_kwh=consumption_kwh,
                feed_in_kwh=feed_in_kwh,
                shared_kwh=shared_kwh,
            )
        )

    def order(balance: LegBalance) -> tuple:
        """Rank one LEG on the production-heavy to consumption-heavy scale."""
        if not balance.metering_points:
            return (1, 0.0, text_key(balance.name))
        ratio = balance.producers_per_consumer
        # No consumers means no quotient, and it is the extreme end of the
        # very scale this sorts on -- so it leads rather than being special.
        return (0, -float("inf") if ratio is None else -ratio, text_key(balance.name))

    return sorted(balances, key=order)


@dataclass
class AreaPotential:
    """How much of one Trafokreis is already in, and how much is not.

    The counted Wohneinheiten come from `site.dwelling_count`, typed in while
    walking the neighbourhood. They cannot be derived: a metering point only
    exists once somebody has signed up, so the meters answer who is already
    in and never how many there could be.
    """

    substation_area_id: int
    name: str
    bkw_designation: str
    sites: int
    sites_counted: int
    dwellings: int
    participating: int

    @property
    def sites_open(self) -> int:
        """Addresses in this Trafokreis nobody has counted yet."""
        return self.sites - self.sites_counted

    @property
    def is_counted(self) -> bool:
        """Whether anything at all has been counted here."""
        return self.sites_counted > 0

    @property
    def open_dwellings(self) -> Optional[int]:
        """Counted Wohneinheiten that are not participating yet.

        `None` while nothing is counted -- that is "unknown", not "none
        left", and the two must not look alike. Floored at zero: a
        participant can hold a meter at an address counted lower than the
        number of parties actually signed up, and a negative potential is
        not a statement about anything.
        """
        if not self.is_counted:
            return None
        return max(0, self.dwellings - self.participating)

    @property
    def participating_share(self) -> Optional[float]:
        """Participating as a percentage of the counted Wohneinheiten."""
        if not self.dwellings:
            return None
        return self.participating / self.dwellings * 100


def substation_area_potential(connection: sqlite3.Connection) -> list[AreaPotential]:
    """What each Trafokreis holds, and how much of it is not in yet.

    Reports and grades nothing, like `leg_balance`: no threshold, no colour
    and no verdict word. What stands in for a verdict is the **ordering** --
    most uncounted-for Wohneinheiten first, so the Trafokreis with the most
    left to win is where the eye lands. A Trafokreis nobody has counted is
    pushed past every counted one but named, because one nobody surveyed
    otherwise looks exactly like one with no potential.

    `participating` is a **proxy**: one party per person holding a consumption
    assignment at an address in this Trafokreis that has not ended. A
    household that signed up with two meters counts once; two households
    sharing one contract party count once too.

    **Not ended, not `covers(today)`** -- and only the real data showed why.
    All 123 assignments in the live deployment start in the *future* (114 on
    2027-01-01, 9 on 2026-12-01), because the LEG has not begun operating.
    Judged on today, every single participant would have counted as
    untouched potential, and the view would have sent the administrator
    knocking on the doors of people who had already signed.
    """
    areas = substation_area_repo.list_all(connection)
    sites = site_repo.list_all(connection)
    points = {point.id: point for point in metering_point_repo.list_all(connection)}
    now = datetime.now()

    sites_by_area: dict[Optional[int], list] = {}
    for site in sites:
        sites_by_area.setdefault(site.substation_area_id, []).append(site)

    # One party per person per Trafokreis: the same person at two addresses
    # in one area is one participating household there.
    persons_by_area: dict[Optional[int], set[int]] = {}
    site_area = {site.id: site.substation_area_id for site in sites}
    for assignment in assignment_repo.list_all(connection):
        if not assignment.is_current_or_upcoming(now):
            continue
        point = points.get(assignment.metering_point_id)
        if point is None or point.direction != DIRECTION_CONSUMPTION:
            continue
        area_id = site_area.get(point.site_id)
        if area_id is None:
            continue
        persons_by_area.setdefault(area_id, set()).add(assignment.person_id)

    potentials = []
    for area in areas:
        own = sites_by_area.get(area.id, [])
        counted = [site.dwelling_count for site in own if site.dwelling_count is not None]
        potentials.append(
            AreaPotential(
                substation_area_id=area.id,
                name=area.name,
                bkw_designation=area.bkw_designation or "",
                sites=len(own),
                sites_counted=len(counted),
                dwellings=sum(counted),
                participating=len(persons_by_area.get(area.id, set())),
            )
        )

    def order(potential: AreaPotential) -> tuple:
        """Most left to win first; an uncounted Trafokreis last but named."""
        if potential.open_dwellings is None:
            return (1, 0, text_key(potential.name))
        return (0, -potential.open_dwellings, text_key(potential.name))

    return sorted(potentials, key=order)
