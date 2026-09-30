"""Trend statistics for the Statistik page: energy flow and master-data
growth over a trailing window of calendar months.

Deliberately independent of billing (see `app.domain.billing`): these are
plain aggregates over `readings` and the `created_at` timestamps already
on every master-data table, meant to show "how is this deployment
growing" and "how much energy is flowing", not to compute anything
billable.
"""

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
from app.models import person_onboarding as person_onboarding_repo
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN
from app.models.person_onboarding import STEPS as ONBOARDING_STEPS
from app.sort_keys import text_key


@dataclass
class MonthlyEnergy:
    """One calendar month's total local consumption and feed-in.

    Attributes:
        year: Calendar year.
        month: Calendar month, 1 to 12.
        consumption_kwh: Total consumption recorded that month, in kWh.
        feed_in_kwh: Total feed-in recorded that month, in kWh.
    """

    year: int
    month: int
    consumption_kwh: float
    feed_in_kwh: float

    @property
    def balance_kwh(self) -> float:
        """Feed-in minus consumption -- positive means a net surplus.

        Returns:
            `feed_in_kwh - consumption_kwh`, in kWh.
        """
        return self.feed_in_kwh - self.consumption_kwh


@dataclass
class MonthlyGrowth:
    """Cumulative master-data counts as of the end of one calendar month.

    Attributes:
        year: Calendar year.
        month: Calendar month, 1 to 12.
        persons: Number of persons created on or before this month.
        metering_points: Number of metering points created on or before this month.
        sites: Number of sites created on or before this month.
        substation areas: Number of substation areas created on or before this month.
        legs: Number of LEGs created on or before this month.
    """

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
    """Aggregate consumption/feed-in totals per month over a trailing window.

    Args:
        connection: Open SQLite connection.
        leg_id: If given, only readings from metering points currently assigned
            to this LEG are counted; `None` aggregates across all LEGs.
        reference_date: Last month of the window; defaults to today.
        months: Number of trailing months to cover.

    Returns:
        One `MonthlyEnergy` per month in `trailing_months`, chronological,
        zero-filled for months with no readings.
    """
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
    """Fetch a table's `created_at` timestamps as plain dates.

    Args:
        connection: Open SQLite connection.
        table: Name of a table with a `created_at` column (one of the
            fixed, internally-known master-data tables -- never
            user-supplied).

    Returns:
        The `created_at` values, parsed to `date`.
    """
    # nosec B608 -- `table` is one of the fixed table names above, never user input
    rows = connection.execute(f"SELECT created_at FROM {table}").fetchall()  # nosec B608
    return [datetime.fromisoformat(row["created_at"]).date() for row in rows if row["created_at"]]


def monthly_growth_counts(
    connection: sqlite3.Connection,
    reference_date: Optional[date] = None,
    months: int = 12,
) -> list[MonthlyGrowth]:
    """Compute cumulative master-data counts per month over a trailing window.

    Args:
        connection: Open SQLite connection.
        reference_date: Last month of the window; defaults to today.
        months: Number of trailing months to cover.

    Returns:
        One `MonthlyGrowth` per month in `trailing_months`, chronological.
    """
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
    """What a quarter's imported data actually contains, for one LEG or all.

    Exists to answer two questions the app used to leave unanswered:
    which quarter is worth billing, and did the last import land the way
    it should have. Both are judged by eye from these figures -- a
    consumption total an order of magnitude off, or metering points
    missing from the import, is obvious here and invisible everywhere
    else.

    Attributes:
        year: Calendar year.
        quarter: Quarter number, 1 to 4.
        consumption_kwh: Total consumption recorded in the quarter.
        feed_in_kwh: Total feed-in recorded in the quarter.
        reading_count: Number of 15-minute reading rows.
        metering_points_with_readings: How many distinct metering points
            contributed at least one reading.
        metering_points_expected: How many metering points held an
            assignment overlapping the quarter -- what the import should
            have covered.
        last_import_at: ISO timestamp of the most recent import batch
            behind these readings, or `None` for demo/manually seeded
            data that came from no batch.
        import_sources: Distinct `readings.source` values present,
            sorted -- "ebix", "csv" or "demo".
    """

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
        """Whether local sharing is possible at all in this quarter.

        Sharing is `min(production, consumption)` per interval, so a
        quarter missing either direction entirely can only ever produce
        zero -- and a billing run over it, while perfectly legitimate,
        yields documents reading 0.00 throughout.
        """
        return self.consumption_kwh > 0 and self.feed_in_kwh > 0

    @property
    def missing_metering_points(self) -> int:
        """Metering points that were assigned but delivered no readings.

        The single most useful number for spotting a partial import.
        """
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
    """Summarise every quarter that has readings, newest first.

    Args:
        connection: Open SQLite connection.
        leg_id: Restrict to metering points currently assigned to this
            LEG; `None` aggregates across all LEGs.

    Returns:
        One `QuarterEnergy` per quarter with at least one reading,
        ordered newest first.
    """
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
    """Count metering points whose assignment overlaps a quarter.

    This is what an import should have covered -- deliberately the same
    overlap rule the distribution uses to decide who takes part (see
    `app.domain.distribution._seed_participants`), so the two numbers are
    comparable and a shortfall really does mean missing data.

    Args:
        connection: Open SQLite connection.
        year: Calendar year of the quarter.
        quarter: Quarter number, 1 to 4.
        leg_id: Restrict to this LEG, or `None` for all.

    Returns:
        The number of distinct metering points.
    """
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
    """Installed PV power and battery capacity, summed over plausible values.

    Purely informational -- nothing bills from this, and no check gates on
    it. What it must not do is look complete when it is not: a capacity is
    typed in by hand per metering point (see
    `app.gui.metering_point_form`), so a bare sum invites being read as the
    LEG's total when in truth several meters carry no figure at all. Hence
    the counts alongside each sum.

    Attributes:
        pv_kwp: Sum of plausible `pv_capacity_kwp` values, in kWp. Note
            this is power, not energy -- the energy actually fed in per
            quarter is `QuarterEnergy.feed_in_kwh`.
        pv_counted: How many metering points contributed to `pv_kwp`.
        pv_expected: How many feed-in metering points exist in scope,
            whether or not they carry a figure. `pv_counted` below this
            means the sum is incomplete.
        battery_kwh: Sum of plausible `battery_capacity_kwh` values.
        battery_counted: How many metering points contributed to
            `battery_kwh`. There is no "expected" counterpart: a metering
            point without a battery is the normal case, not a gap.
        implausible: Designations of metering points whose value was
            discarded, so a dropped figure is never silent (same reasoning
            as `QuarterEnergy.missing_metering_points`).
    """

    pv_kwp: float
    pv_counted: int
    pv_expected: int
    battery_kwh: float
    battery_counted: int
    implausible: list[str]


def _plausible_capacity(value: Optional[float]) -> bool:
    """Whether a hand-entered capacity can be summed.

    Args:
        value: The stored value, possibly `None`.

    Returns:
        `True` for a value that is set, greater than zero and below
        `CAPACITY_PLAUSIBLE_MAX`. Zero is excluded deliberately: a metering
        point with no PV is recorded by leaving the field empty, so a zero
        adds nothing and a negative is impossible in reality.
    """
    return value is not None and 0 < value < CAPACITY_PLAUSIBLE_MAX


def installed_capacity_totals(
    connection: sqlite3.Connection, leg_id: Optional[int] = None
) -> InstalledCapacity:
    """Sum the installed PV power and battery capacity on record.

    Args:
        connection: Open SQLite connection.
        leg_id: Restrict to one LEG, or `None` for every metering point.

    Returns:
        The `InstalledCapacity`. A PV figure on a **consumption** metering
        point is discarded: installed production belongs on the feed-in
        side, and the field is editable on both. Battery capacity is
        accepted on either side -- a storage unit sits behind the
        connection, not behind one direction.
    """
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
    """One point on the energy chart's x-axis.

    Attributes:
        start: The bucket's first moment, as `buckets_in` produced it.
        consumption_kwh: Everything drawn in this bucket.
        feed_in_kwh: Everything fed in.
        shared_kwh: How much of the feed-in actually found a taker inside
            the LEG -- `min(P, C)` **per 15-minute interval**, then summed.
            See `energy_series` for why that distinction is the whole
            point of the number.
    """

    start: datetime
    consumption_kwh: float
    feed_in_kwh: float
    shared_kwh: float

    @property
    def self_consumption_share(self) -> Optional[float]:
        """How much of the local production was used locally, 0 to 1.

        Returns:
            `shared_kwh / feed_in_kwh`, or `None` when nothing was fed in
            -- a bucket at night has no share, which is a different
            statement from "a share of zero" and must not be drawn as one.
        """
        if self.feed_in_kwh <= 0:
            return None
        return self.shared_kwh / self.feed_in_kwh

    @property
    def local_coverage_share(self) -> Optional[float]:
        """How much of the consumption was covered locally, 0 to 1.

        Returns:
            `shared_kwh / consumption_kwh`, or `None` when nothing was
            drawn.
        """
        if self.consumption_kwh <= 0:
            return None
        return self.shared_kwh / self.consumption_kwh

    def value_for(self, granularity_key: str) -> tuple[float, float, float]:
        """The three figures in the unit that resolution is drawn in.

        At the app's own 15-minute resolution a bucket holds exactly one
        interval, so the natural reading is power: the load curve everyone
        recognises. Anything coarser is an amount of energy.

        Args:
            granularity_key: One of `app.domain.period`'s `GRANULARITY_*`.

        Returns:
            `(consumption, feed_in, shared)` in kW at quarter-hour
            resolution and in kWh otherwise.
        """
        values = (self.consumption_kwh, self.feed_in_kwh, self.shared_kwh)
        if granularity_key != GRANULARITY_QUARTER_HOUR:
            return values
        factor = 60 / INTERVAL_MINUTES
        return tuple(round(value * factor, 3) for value in values)


def energy_unit(granularity_key: str) -> str:
    """The unit the energy chart's y-axis carries at one resolution.

    Args:
        granularity_key: One of `app.domain.period`'s `GRANULARITY_*`.

    Returns:
        `"kW"` at quarter-hour resolution, `"kWh"` otherwise.
    """
    return "kW" if granularity_key == GRANULARITY_QUARTER_HOUR else "kWh"


def energy_series(
    connection: sqlite3.Connection,
    granularity_key: str,
    window: tuple[datetime, datetime],
    leg_id: Optional[int] = None,
) -> list[EnergyBucket]:
    """Consumption, feed-in and locally shared energy over one window.

    **The shared figure is formed per 15-minute interval and only then
    summed, and getting that backwards is the expensive mistake here.**
    `min(daily P, daily C)` would claim energy was shared when production
    happened at noon and consumption in the evening -- a number that looks
    entirely plausible and is simply false. The rule is the one
    `app.domain.distribution` bills on (`S(t) = min(P(t), C(t))`, see its
    module docstring); if that ever changes, this has to change with it,
    or the chart and the invoices will tell different stories about the
    same quarter.

    Like the distribution, this counts **every** reading of the LEG,
    assigned or not: whether energy could be attributed to somebody
    decides who pays for it, not whether it was shared.

    Args:
        connection: Open SQLite connection.
        granularity_key: One of `app.domain.period`'s `GRANULARITY_*`.
        window: `(start, end_exclusive)` from `period.window_for`.
        leg_id: Restrict to one LEG, or `None` for all of them. With
            `None` the shared figure is the sum over the LEGs computed
            separately -- energy is only ever shared *within* one LEG, so
            pooling every reading first would invent sharing between
            neighbours who have nothing to do with each other.

    Returns:
        One `EnergyBucket` per bucket in the window, oldest first --
        including the empty ones, so the axis keeps its shape.
    """
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
    """One point on the receivables chart.

    Every figure covers the **whole LEG**. Nothing here is ever broken
    down per person, and that is a decision rather than an omission: the
    administrator asked for it explicitly on data-protection grounds. A
    single member's balance is a matter for that member's own detail page,
    not for a chart anybody glancing at the screen can read.

    Attributes:
        start: The bucket's first moment.
        invoiced_rappen: Net amount billed in this bucket, from the
            billing runs created in it.
        received_rappen: Money that actually arrived in this bucket.
        open_rappen: Everything invoiced up to the end of this bucket
            minus everything received up to then -- the running mountain
            of receivables, which is meant to come down.
    """

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
    """Invoiced, received and still-open amounts over one window.

    `open_rappen` is **cumulative from the beginning of time**, not just
    within the window: an outstanding amount does not stop existing
    because the chart starts later. So the line begins at whatever was
    already open when the window opens, and every bucket adds that
    bucket's invoices and subtracts its payments.

    Amounts are read as `app.models.account_entry` stores them and are
    **not** negated here. That module's sign convention is that an
    incoming payment is stored negative, because it reduces a debt; the
    single negation in this app happens where a person's balance is
    displayed, and adding a second one is how a sign bug gets in.

    Args:
        connection: Open SQLite connection.
        granularity_key: One of `app.domain.period`'s `GRANULARITY_*`.
        window: `(start, end_exclusive)` from `period.window_for`.

    Returns:
        One `ReceivablesBucket` per bucket in the window, oldest first.
    """
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
    """One step of the onboarding pipeline and how many people wait at it.

    Attributes:
        label: The step's German name, from
            `app.models.person_onboarding.STEPS`.
        waiting: How many in-progress onboardings have this step as their
            current one -- people whose previous steps are all dated and
            who are waiting on this one.
    """

    label: str
    waiting: int


def onboarding_funnel(connection: sqlite3.Connection) -> tuple[list[FunnelStep], int]:
    """Where the membership pipeline is stuck, step by step.

    A count per step rather than a cumulative funnel: the question worth
    answering is "what is holding people up", and that is the step they
    are sitting on, not how many got past it. In this deployment it puts
    61 of 88 open onboardings on "Bestätigung durch die BKW" -- a number
    that was in the database all along and nowhere on a screen.

    Args:
        connection: Open SQLite connection.

    Returns:
        `(steps, completed)`: one `FunnelStep` per step in
        `person_onboarding.STEPS` order, and how many onboardings are
        finished.
    """
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
    """One LEG's share of the deployment.

    Attributes:
        leg_id: The LEG.
        name: Its name.
        feed_in_metering_points: Feed-in meters assigned to it.
        consumption_metering_points: Consumption meters assigned to it.
        pv_kwp: Installed PV power on record for it, in kWp.
    """

    leg_id: int
    name: str
    feed_in_metering_points: int
    consumption_metering_points: int
    pv_kwp: float

    @property
    def metering_points(self) -> int:
        """Both directions together.

        Returns:
            The LEG's total metering point count.
        """
        return self.feed_in_metering_points + self.consumption_metering_points


def distribution_by_leg(connection: sqlite3.Connection) -> list[LegDistribution]:
    """How the metering points and the installed power sit across the LEGs.

    Scoped by `metering_point.leg_id`, never by the sites those meters sit
    at: LEG membership is a property of the metering point, and two meters
    at one address can belong to different LEGs (see
    `app.domain.participant_mix.compute_participant_mix_for_leg`, where
    going via the sites once pulled a neighbour's meter into a LEG's
    figures).

    Args:
        connection: Open SQLite connection.

    Returns:
        One `LegDistribution` per LEG, largest first -- the order that
        makes an imbalance visible at a glance.
    """
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
