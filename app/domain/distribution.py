"""The core 15-minute local-solar distribution engine (project brief, section 5)."""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.domain.period import months_in_quarter, quarter_bounds
from app.models import metering_point as metering_point_repo
from app.models import assignment as assignment_repo
from app.models.metering_point import DIRECTION_CONSUMPTION
from app.models.reading import list_readings_in_period

#: Number of decimal places internal kWh totals are rounded to.
KWH_PRECISION = 3


class LegNotAssignedError(Exception):
    """Raised when a MeteringPoint with readings in the requested quarter has no LEG assigned."""


@dataclass
class MeteringPointQuarterResult:
    """One metering point's locally shared energy totals for a quarter."""

    metering_point_id: int
    consumed_local_kwh: float = 0.0
    produced_local_kwh: float = 0.0


@dataclass
class PersonQuarterResult:
    """One person's locally shared energy totals for a quarter, within one LEG."""

    person_id: int
    consumed_local_kwh: float = 0.0
    produced_local_kwh: float = 0.0
    consumed_by_month: dict[int, float] = field(default_factory=dict)
    produced_by_month: dict[int, float] = field(default_factory=dict)
    by_metering_point: dict[int, MeteringPointQuarterResult] = field(default_factory=dict)


@dataclass
class DistributionResult:
    """Result of running the distribution engine over one quarter, for one LEG."""

    leg_id: int
    year: int
    quarter: int
    person_results: dict[int, PersonQuarterResult] = field(default_factory=dict)
    unassigned_kwh: float = 0.0
    interval_count: int = 0

    def total_consumed_local_kwh(self) -> float:
        """Sum of locally-sourced consumption across all persons."""
        return sum(r.consumed_local_kwh for r in self.person_results.values())

    def total_produced_local_kwh(self) -> float:
        """Sum of locally-delivered production across all persons."""
        return sum(r.produced_local_kwh for r in self.person_results.values())


def _person_at(
    assignments_by_metering_point: dict[int, list],
    metering_point_id: int,
    moment: datetime,
    cache: dict[tuple[int, date], "int | None"],
) -> "int | None":
    """Resolve which Person a MeteringPoint belonged to at a given moment."""
    key = (metering_point_id, moment.date())
    if key in cache:
        return cache[key]

    person_id = None
    for assignment in assignments_by_metering_point.get(metering_point_id, []):
        if assignment.covers(moment):
            person_id = assignment.person_id
            break

    cache[key] = person_id
    return person_id


def _load_leg_and_designation_by_metering_point(
    connection: sqlite3.Connection,
) -> tuple[dict[int, "int | None"], dict[int, str]]:
    """Build MeteringPoint lookups needed to group readings by LEG."""
    leg_id_by_metering_point: dict[int, "int | None"] = {}
    designation_by_metering_point: dict[int, str] = {}
    for metering_point in metering_point_repo.list_all(connection):
        leg_id_by_metering_point[metering_point.id] = metering_point.leg_id
        designation_by_metering_point[metering_point.id] = metering_point.designation
    return leg_id_by_metering_point, designation_by_metering_point


def _seed_participants(
    result: DistributionResult,
    assignments_by_metering_point: dict[int, list],
    quarter_start: date,
    quarter_end_exclusive: date,
) -> None:
    """Enter every participant of the quarter into the result, at zero."""
    last_day = quarter_end_exclusive - timedelta(days=1)
    for metering_point_id, assignments in assignments_by_metering_point.items():
        for assignment in assignments:
            overlaps = assignment.valid_from <= last_day and (
                assignment.valid_to is None or assignment.valid_to >= quarter_start
            )
            if not overlaps:
                continue
            person_result = result.person_results.setdefault(
                assignment.person_id, PersonQuarterResult(person_id=assignment.person_id)
            )
            person_result.by_metering_point.setdefault(
                metering_point_id, MeteringPointQuarterResult(metering_point_id=metering_point_id)
            )


def compute_quarter_distribution(
    connection: sqlite3.Connection, leg_id: int, year: int, quarter: int
) -> DistributionResult:
    """Run the distribution engine over one calendar quarter, for one LEG."""
    start, end = quarter_bounds(year, quarter)
    rows = list_readings_in_period(connection, start.isoformat(), end.isoformat())

    leg_id_by_metering_point, designation_by_metering_point = _load_leg_and_designation_by_metering_point(
        connection
    )
    missing_metering_point_ids = sorted(
        {row["metering_point_id"] for row in rows}
        - {mp_id for mp_id, mp_leg_id in leg_id_by_metering_point.items() if mp_leg_id is not None}
    )
    if missing_metering_point_ids:
        designations = [
            designation_by_metering_point.get(mp_id, f"#{mp_id}") for mp_id in missing_metering_point_ids
        ]
        raise LegNotAssignedError(
            "Folgende Messpunkte mit Messdaten in diesem Quartal sind noch "
            "keiner LEG zugeordnet: " + ", ".join(designations) + ". "
            "Bitte zuerst unter „Messpunkte“ die LEG zuweisen -- lokale "
            "Verteilung ist nur innerhalb derselben LEG möglich."
        )

    leg_rows = [row for row in rows if leg_id_by_metering_point[row["metering_point_id"]] == leg_id]

    # Assignments are loaded for every metering point of this LEG, not just
    # the ones with readings: a metering point that delivered nothing this
    # quarter still belongs on its owner's bill (see `_seed_participants`).
    assignments_by_metering_point: dict[int, list] = {
        metering_point_id: assignment_repo.list_for_metering_point(connection, metering_point_id)
        for metering_point_id, mp_leg_id in leg_id_by_metering_point.items()
        if mp_leg_id == leg_id
    }

    result = DistributionResult(leg_id=leg_id, year=year, quarter=quarter)
    _seed_participants(result, assignments_by_metering_point, start.date(), end.date())
    person_cache: dict[tuple[int, date], "int | None"] = {}

    result.interval_count = len({row["timestamp"] for row in leg_rows})

    intervals: dict[str, list[sqlite3.Row]] = {}
    for row in leg_rows:
        intervals.setdefault(row["timestamp"], []).append(row)

    for timestamp_text, interval_rows in intervals.items():
        moment = datetime.fromisoformat(timestamp_text)
        production_total = sum(r["kwh"] for r in interval_rows if r["direction"] != DIRECTION_CONSUMPTION)
        consumption_total = sum(r["kwh"] for r in interval_rows if r["direction"] == DIRECTION_CONSUMPTION)
        shared = min(production_total, consumption_total)

        for row in interval_rows:
            is_consumption = row["direction"] == DIRECTION_CONSUMPTION
            denominator = consumption_total if is_consumption else production_total
            local_share = row["kwh"] * shared / denominator if denominator > 0 else 0.0
            if local_share == 0.0:
                continue

            person_id = _person_at(
                assignments_by_metering_point, row["metering_point_id"], moment, person_cache
            )
            if person_id is None:
                result.unassigned_kwh += local_share
                continue

            person_result = result.person_results.setdefault(
                person_id, PersonQuarterResult(person_id=person_id)
            )
            metering_point_id = row["metering_point_id"]
            metering_point_result = person_result.by_metering_point.setdefault(
                metering_point_id, MeteringPointQuarterResult(metering_point_id=metering_point_id)
            )
            month = moment.month
            if is_consumption:
                person_result.consumed_local_kwh += local_share
                person_result.consumed_by_month[month] = (
                    person_result.consumed_by_month.get(month, 0.0) + local_share
                )
                metering_point_result.consumed_local_kwh += local_share
            else:
                person_result.produced_local_kwh += local_share
                person_result.produced_by_month[month] = (
                    person_result.produced_by_month.get(month, 0.0) + local_share
                )
                metering_point_result.produced_local_kwh += local_share

    quarter_months = [month for _, month in months_in_quarter(year, quarter)]
    for person_result in result.person_results.values():
        # Ensure every month of the quarter has an entry (zero if unused)
        # so billing documents always show a complete monthly breakdown.
        for month in quarter_months:
            person_result.consumed_by_month.setdefault(month, 0.0)
            person_result.produced_by_month.setdefault(month, 0.0)

        person_result.consumed_local_kwh = round(person_result.consumed_local_kwh, KWH_PRECISION)
        person_result.produced_local_kwh = round(person_result.produced_local_kwh, KWH_PRECISION)
        for metering_point_result in person_result.by_metering_point.values():
            metering_point_result.consumed_local_kwh = round(
                metering_point_result.consumed_local_kwh, KWH_PRECISION
            )
            metering_point_result.produced_local_kwh = round(
                metering_point_result.produced_local_kwh, KWH_PRECISION
            )
        for month in quarter_months:
            person_result.consumed_by_month[month] = round(
                person_result.consumed_by_month[month], KWH_PRECISION
            )
            person_result.produced_by_month[month] = round(
                person_result.produced_by_month[month], KWH_PRECISION
            )
    result.unassigned_kwh = round(result.unassigned_kwh, KWH_PRECISION)

    return result
