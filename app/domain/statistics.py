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

from app.domain.period import month_bounds, quarter_bounds, trailing_months


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
