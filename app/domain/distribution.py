"""The core 15-minute local-solar distribution engine (project brief, section 5).

For every 15-minute interval `t` in a billing quarter:

1. `P(t)` = sum of all production meter readings at `t`.
2. `C(t)` = sum of all consumption meter readings at `t`.
3. `S(t) = min(P(t), C(t))` -- only energy produced *and* consumed at the
   same instant can be shared locally.
4. Each consumption meter's locally-covered share is
   `consumption_m(t) * S(t) / C(t)` (zero if `C(t) == 0`).
5. Each production meter's locally-delivered share is
   `production_m(t) * S(t) / P(t)` (zero if `P(t) == 0`).

Each meter's interval share is then attributed to whichever participant was
assigned to it at that exact moment (see `app.models.assignment`), so a
mid-quarter tenant change splits a meter's energy between two participants
automatically.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.period import months_in_quarter, quarter_bounds
from app.models import assignment as assignment_repo
from app.models.meter import CONSUMPTION_ROLES
from app.models.reading import list_readings_in_period

#: Number of decimal places internal kWh totals are rounded to.
KWH_PRECISION = 3


@dataclass
class ParticipantQuarterResult:
    """One participant's locally shared energy totals for a quarter.

    Attributes:
        participant_id: The participant these totals belong to.
        consumed_local_kwh: Total locally-sourced energy this participant
            consumed (billable via an invoice), rounded to
            `KWH_PRECISION` decimals.
        produced_local_kwh: Total locally-delivered energy this participant
            produced (creditable via a credit note), rounded to
            `KWH_PRECISION` decimals.
        consumed_by_month: Locally-sourced consumption, keyed by calendar
            month (1-12), rounded to `KWH_PRECISION` decimals. Always has
            one entry per month of the quarter, including zero-valued ones,
            so the billing document can show a complete monthly breakdown.
        produced_by_month: Locally-delivered production, keyed by calendar
            month (1-12), same rounding and completeness as
            `consumed_by_month`.
    """

    participant_id: int
    consumed_local_kwh: float = 0.0
    produced_local_kwh: float = 0.0
    consumed_by_month: dict[int, float] = field(default_factory=dict)
    produced_by_month: dict[int, float] = field(default_factory=dict)


@dataclass
class DistributionResult:
    """Result of running the distribution engine over one quarter.

    Attributes:
        year: Calendar year of the billing quarter.
        quarter: Quarter number, 1 to 4.
        participant_results: Per-participant totals, keyed by participant id.
        unassigned_kwh: Locally shared energy that could not be attributed
            to any participant because no assignment covered that meter at
            that moment (an assignment gap). Should be zero for clean data;
            surfaced to the plausibility checks (section 7) otherwise.
        interval_count: Number of distinct 15-minute intervals processed.
    """

    year: int
    quarter: int
    participant_results: dict[int, ParticipantQuarterResult] = field(default_factory=dict)
    unassigned_kwh: float = 0.0
    interval_count: int = 0

    def total_consumed_local_kwh(self) -> float:
        """Sum of locally-sourced consumption across all participants.

        Returns:
            The total in kWh.
        """
        return sum(r.consumed_local_kwh for r in self.participant_results.values())

    def total_produced_local_kwh(self) -> float:
        """Sum of locally-delivered production across all participants.

        Returns:
            The total in kWh.
        """
        return sum(r.produced_local_kwh for r in self.participant_results.values())


def _participant_at(
    assignments_by_meter: dict[int, list],
    meter_id: int,
    moment: datetime,
    cache: dict[tuple[int, date], "int | None"],
) -> "int | None":
    """Resolve which participant a meter belonged to at a given moment.

    Results are cached per `(meter_id, date)` since assignments only ever
    change at day granularity, which turns what would be one lookup per
    15-minute interval into one lookup per meter per day.

    Args:
        assignments_by_meter: Pre-loaded assignments, keyed by meter id.
        meter_id: Meter to resolve.
        moment: Interval timestamp to resolve at.
        cache: Mutable memoization cache, shared across calls for one run.

    Returns:
        The participant id valid at that moment, or `None` if no
        assignment covers it (a gap in the assignment history).
    """
    key = (meter_id, moment.date())
    if key in cache:
        return cache[key]

    participant_id = None
    for assignment in assignments_by_meter.get(meter_id, []):
        if assignment.covers(moment):
            participant_id = assignment.participant_id
            break

    cache[key] = participant_id
    return participant_id


def compute_quarter_distribution(
    connection: sqlite3.Connection, year: int, quarter: int
) -> DistributionResult:
    """Run the distribution engine over one calendar quarter.

    Args:
        connection: Open SQLite connection.
        year: Calendar year of the billing quarter.
        quarter: Quarter number, 1 to 4.

    Returns:
        A `DistributionResult` with per-participant totals for the quarter.
    """
    start, end = quarter_bounds(year, quarter)
    rows = list_readings_in_period(connection, start.isoformat(), end.isoformat())

    assignments_by_meter: dict[int, list] = {}
    for meter_id in {row["meter_id"] for row in rows}:
        assignments_by_meter[meter_id] = assignment_repo.list_for_meter(connection, meter_id)

    result = DistributionResult(year=year, quarter=quarter)
    participant_cache: dict[tuple[int, date], "int | None"] = {}

    intervals: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        intervals.setdefault(row["timestamp"], []).append(row)

    result.interval_count = len(intervals)

    for timestamp_text, interval_rows in intervals.items():
        moment = datetime.fromisoformat(timestamp_text)
        production_total = sum(
            r["kwh"] for r in interval_rows if r["role"] not in CONSUMPTION_ROLES
        )
        consumption_total = sum(
            r["kwh"] for r in interval_rows if r["role"] in CONSUMPTION_ROLES
        )
        shared = min(production_total, consumption_total)

        for row in interval_rows:
            is_consumption = row["role"] in CONSUMPTION_ROLES
            denominator = consumption_total if is_consumption else production_total
            local_share = row["kwh"] * shared / denominator if denominator > 0 else 0.0
            if local_share == 0.0:
                continue

            participant_id = _participant_at(
                assignments_by_meter, row["meter_id"], moment, participant_cache
            )
            if participant_id is None:
                result.unassigned_kwh += local_share
                continue

            participant_result = result.participant_results.setdefault(
                participant_id, ParticipantQuarterResult(participant_id=participant_id)
            )
            month = moment.month
            if is_consumption:
                participant_result.consumed_local_kwh += local_share
                participant_result.consumed_by_month[month] = (
                    participant_result.consumed_by_month.get(month, 0.0) + local_share
                )
            else:
                participant_result.produced_local_kwh += local_share
                participant_result.produced_by_month[month] = (
                    participant_result.produced_by_month.get(month, 0.0) + local_share
                )

    quarter_months = [month for _, month in months_in_quarter(year, quarter)]
    for participant_result in result.participant_results.values():
        # Ensure every month of the quarter has an entry (zero if unused)
        # so billing documents always show a complete monthly breakdown.
        for month in quarter_months:
            participant_result.consumed_by_month.setdefault(month, 0.0)
            participant_result.produced_by_month.setdefault(month, 0.0)

        participant_result.consumed_local_kwh = round(
            participant_result.consumed_local_kwh, KWH_PRECISION
        )
        participant_result.produced_local_kwh = round(
            participant_result.produced_local_kwh, KWH_PRECISION
        )
        for month in quarter_months:
            participant_result.consumed_by_month[month] = round(
                participant_result.consumed_by_month[month], KWH_PRECISION
            )
            participant_result.produced_by_month[month] = round(
                participant_result.produced_by_month[month], KWH_PRECISION
            )
    result.unassigned_kwh = round(result.unassigned_kwh, KWH_PRECISION)

    return result
