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
from datetime import date, datetime
from typing import Optional

from app.domain.period import month_bounds, trailing_months


@dataclass
class MonthlyEnergy:
    """One calendar month's total local Bezug and Einspeisung.

    Attributes:
        year: Calendar year.
        month: Calendar month, 1 to 12.
        bezug_kwh: Total consumption recorded that month, in kWh.
        einspeisung_kwh: Total feed-in recorded that month, in kWh.
    """

    year: int
    month: int
    bezug_kwh: float
    einspeisung_kwh: float

    @property
    def saldo_kwh(self) -> float:
        """Feed-in minus consumption -- positive means a net surplus.

        Returns:
            `einspeisung_kwh - bezug_kwh`, in kWh.
        """
        return self.einspeisung_kwh - self.bezug_kwh


@dataclass
class MonthlyGrowth:
    """Cumulative master-data counts as of the end of one calendar month.

    Attributes:
        year: Calendar year.
        month: Calendar month, 1 to 12.
        personen: Number of Personen created on or before this month.
        messpunkte: Number of Messpunkte created on or before this month.
        standorte: Number of Standorte created on or before this month.
        trafokreise: Number of Trafokreise created on or before this month.
        legs: Number of LEGs created on or before this month.
    """

    year: int
    month: int
    personen: int
    messpunkte: int
    standorte: int
    trafokreise: int
    legs: int


def monthly_energy_totals(
    connection: sqlite3.Connection,
    leg_id: Optional[int] = None,
    reference_date: Optional[date] = None,
    months: int = 12,
) -> list[MonthlyEnergy]:
    """Aggregate Bezug/Einspeisung totals per month over a trailing window.

    Args:
        connection: Open SQLite connection.
        leg_id: If given, only readings from Messpunkte currently assigned
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
        JOIN messpunkt mp ON mp.id = r.messpunkt_id
        WHERE r.timestamp >= ? AND r.timestamp < ?
    """
    params: list = [start, end]
    if leg_id is not None:
        query += " AND mp.leg_id = ?"
        params.append(leg_id)
    query += " GROUP BY ym, r.direction"

    totals: dict[str, dict[str, float]] = {}
    for row in connection.execute(query, params):
        totals.setdefault(row["ym"], {"bezug": 0.0, "einspeisung": 0.0})[row["direction"]] = row["total"]

    return [
        MonthlyEnergy(
            year=year,
            month=month,
            bezug_kwh=round(totals.get(f"{year:04d}-{month:02d}", {}).get("bezug", 0.0), 3),
            einspeisung_kwh=round(totals.get(f"{year:04d}-{month:02d}", {}).get("einspeisung", 0.0), 3),
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
    rows = connection.execute(f"SELECT created_at FROM {table}").fetchall()
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

    personen = _creation_dates(connection, "person")
    messpunkte = _creation_dates(connection, "messpunkt")
    standorte = _creation_dates(connection, "standort")
    trafokreise = _creation_dates(connection, "trafokreis")
    legs = _creation_dates(connection, "leg")

    results = []
    for year, month in window:
        _, last_day = month_bounds(year, month)
        results.append(
            MonthlyGrowth(
                year=year,
                month=month,
                personen=sum(1 for d in personen if d <= last_day),
                messpunkte=sum(1 for d in messpunkte if d <= last_day),
                standorte=sum(1 for d in standorte if d <= last_day),
                trafokreise=sum(1 for d in trafokreise if d <= last_day),
                legs=sum(1 for d in legs if d <= last_day),
            )
        )
    return results
