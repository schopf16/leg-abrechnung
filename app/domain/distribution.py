"""The core 15-minute local-solar distribution engine (project brief, section 5).

Sharing happens **within one LEG at a time**, never across LEGs: two
metering points can only exchange energy if they belong to the same LEG
(project requirement -- "es soll nicht über alle Messstationen
ausgeglichen werden, sondern nur innerhalb der Trafostation"; LEG
membership is a property of the individual MeteringPoint, see
`app.models.leg` -- by default a LEG matches one physical substation area, but
it can deliberately span several, see `app.domain.leg_composition`). Every
MeteringPoint with readings in the requested quarter must therefore have a
resolved LEG before this runs at all -- regardless of which LEG's
distribution is actually being computed, since an unassigned MeteringPoint
silently never appearing in *any* LEG's run would be a worse,
harder-to-notice failure than a loud one; see `LegNotAssignedError`.

For every 15-minute interval `t`, independently per LEG:

1. `P(t)` = sum of all feed-in readings at `t` on that LEG.
2. `C(t)` = sum of all consumption readings at `t` on that LEG.
3. `S(t) = min(P(t), C(t))` -- only energy produced *and* consumed at the
   same instant, on the same LEG, can be shared locally.
4. Each consumption-MeteringPoint's locally-covered share is
   `consumption_m(t) * S(t) / C(t)` (zero if `C(t) == 0`).
5. Each feed-in-MeteringPoint's locally-delivered share is
   `production_m(t) * S(t) / P(t)` (zero if `P(t) == 0`).

Each MeteringPoint's interval share is then attributed to whichever Person was
assigned to it at that exact moment (see `app.models.assignment`), so a
mid-quarter move splits a MeteringPoint's energy between two persons
automatically. Moving never changes the MeteringPoint, its site, or that
MeteringPoint's LEG -- only which Person the Assignment points at.

The result covers everyone who took part in the quarter, not only those
whose meters actually moved energy: every person with an Assignment
overlapping the quarter appears, and every metering point they held
appears under them, at zero where nothing was shared. A bill's line-up
must not change from one quarter to the next -- otherwise a recipient
cannot tell whether a missing meter was deliberately left out or simply
forgotten. See `_seed_participants`.

The per-MeteringPoint resolution survives into the result
(`PersonQuarterResult.by_metering_point`) rather than being summed away:
a billing document itemises the shared energy per site and per metering
point, and a participant holding several of them -- a property
management with more than one building -- has to allocate their single
amount internally. Summing the breakdown reproduces the person's totals
exactly; it is the same energy at a finer resolution, never an extra
figure alongside it.
"""

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
    """Raised when a MeteringPoint with readings in the requested quarter has
    no LEG assigned.

    Local sharing is only ever valid within one LEG -- computing a
    distribution while any MeteringPoint's LEG is unknown would risk pooling
    energy between administratively unrelated groups, or silently
    excluding that MeteringPoint from every LEG's billing without anyone
    noticing. The caller must assign a LEG to the offending metering points
    (see the "metering points" page) before a distribution/billing run is
    possible.
    """


@dataclass
class MeteringPointQuarterResult:
    """One metering point's locally shared energy totals for a quarter.

    The distribution engine already computes every share per metering
    point; this keeps that resolution instead of discarding it into the
    person's total, so a billing document can itemise where a
    participant's kWh came from (see `app.pdf.bill_breakdown`). A
    participant holding several metering points -- a property management
    with more than one building -- cannot allocate their amount
    internally from a single summed figure.

    A metering point measures either consumption or feed-in, never both
    (`app.models.metering_point`), so exactly one of the two totals below
    is ever non-zero. Both are kept anyway: the caller groups these by
    site without needing to re-check each metering point's direction.

    Attributes:
        metering_point_id: The metering point these totals belong to.
        consumed_local_kwh: Locally-sourced energy consumed through this
            metering point, rounded to `KWH_PRECISION` decimals.
        produced_local_kwh: Locally-delivered energy fed in through this
            metering point, same rounding.
    """

    metering_point_id: int
    consumed_local_kwh: float = 0.0
    produced_local_kwh: float = 0.0


@dataclass
class PersonQuarterResult:
    """One person's locally shared energy totals for a quarter, within one LEG.

    Attributes:
        person_id: The person these totals belong to.
        consumed_local_kwh: Total locally-sourced energy this person
            consumed (billable via an invoice), rounded to
            `KWH_PRECISION` decimals.
        produced_local_kwh: Total locally-delivered energy this person
            produced (creditable via a credit note), rounded to
            `KWH_PRECISION` decimals.
        consumed_by_month: Locally-sourced consumption, keyed by calendar
            month (1-12), rounded to `KWH_PRECISION` decimals. Always has
            one entry per month of the quarter, including zero-valued ones,
            so the billing document can show a complete monthly breakdown.
        produced_by_month: Locally-delivered production, keyed by calendar
            month (1-12), same rounding and completeness as
            `consumed_by_month`.
        by_metering_point: The same quarter totals broken down per
            metering point, keyed by metering point id. Summing these
            reproduces `consumed_local_kwh`/`produced_local_kwh` exactly
            (both are rounded from the same full-precision figures), so
            this is a finer view of the same energy, never an additional
            one.
    """

    person_id: int
    consumed_local_kwh: float = 0.0
    produced_local_kwh: float = 0.0
    consumed_by_month: dict[int, float] = field(default_factory=dict)
    produced_by_month: dict[int, float] = field(default_factory=dict)
    by_metering_point: dict[int, MeteringPointQuarterResult] = field(default_factory=dict)


@dataclass
class DistributionResult:
    """Result of running the distribution engine over one quarter, for one LEG.

    Attributes:
        leg_id: The LEG this distribution was computed for.
        year: Calendar year of the billing quarter.
        quarter: Quarter number, 1 to 4.
        person_results: Per-person totals, keyed by person id.
        unassigned_kwh: Locally shared energy that could not be attributed
            to any person because no Assignment covered that MeteringPoint at
            that moment (an assignment gap). Should be zero for clean data;
            surfaced to the plausibility checks (section 7) otherwise.
        interval_count: Number of distinct 15-minute intervals processed
            for this LEG.
    """

    leg_id: int
    year: int
    quarter: int
    person_results: dict[int, PersonQuarterResult] = field(default_factory=dict)
    unassigned_kwh: float = 0.0
    interval_count: int = 0

    def total_consumed_local_kwh(self) -> float:
        """Sum of locally-sourced consumption across all persons.

        Returns:
            The total in kWh.
        """
        return sum(r.consumed_local_kwh for r in self.person_results.values())

    def total_produced_local_kwh(self) -> float:
        """Sum of locally-delivered production across all persons.

        Returns:
            The total in kWh.
        """
        return sum(r.produced_local_kwh for r in self.person_results.values())


def _person_at(
    assignments_by_metering_point: dict[int, list],
    metering_point_id: int,
    moment: datetime,
    cache: dict[tuple[int, date], "int | None"],
) -> "int | None":
    """Resolve which Person a MeteringPoint belonged to at a given moment.

    Results are cached per `(metering_point_id, date)` since assignments only
    ever change at day granularity, which turns what would be one lookup
    per 15-minute interval into one lookup per MeteringPoint per day.

    Args:
        assignments_by_metering_point: Pre-loaded assignments, keyed by
            MeteringPoint id.
        metering_point_id: MeteringPoint to resolve.
        moment: Interval timestamp to resolve at.
        cache: Mutable memoization cache, shared across calls for one run.

    Returns:
        The person id valid at that moment, or `None` if no Assignment
        covers it (a gap in the assignment history).
    """
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
    """Build MeteringPoint lookups needed to group readings by LEG.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `(leg_id_by_metering_point, designation_by_metering_point)` pair, both
        keyed by MeteringPoint id. `leg_id_by_metering_point` values are `None` for
        a MeteringPoint with no LEG assigned yet.
    """
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
    """Enter every participant of the quarter into the result, at zero.

    Who took part is decided by the assignments, not by the energy: a
    metering point that shared nothing this quarter -- a flat standing
    empty, a winter with no feed-in at all -- still belongs on its
    owner's bill, with 0 kWh. The line-up of a bill must not change from
    one quarter to the next, or the recipient cannot tell whether a
    missing meter was left out on purpose or forgotten.

    The distribution loop then adds onto these entries, so a participant
    with energy is indistinguishable from one seeded here.

    Args:
        result: The result to seed, modified in place.
        assignments_by_metering_point: Assignments of every metering
            point belonging to this LEG, keyed by metering point id.
        quarter_start: First calendar day of the quarter.
        quarter_end_exclusive: Midnight of the day after the quarter
            ends, as `app.domain.period.quarter_bounds` returns it.

    Returns:
        None.
    """
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
    """Run the distribution engine over one calendar quarter, for one LEG.

    Args:
        connection: Open SQLite connection.
        leg_id: The LEG to compute local sharing for. Only metering points
            belonging to this LEG are considered.
        year: Calendar year of the billing quarter.
        quarter: Quarter number, 1 to 4.

    Returns:
        A `DistributionResult` with per-person totals for the quarter,
        scoped to `leg_id`.

    Raises:
        LegNotAssignedError: If any MeteringPoint *anywhere* (not just on this
            LEG) has readings in this quarter but no resolved LEG -- a
            deployment-wide data-hygiene gate, since such a MeteringPoint
            would otherwise silently never appear in any LEG's run.
    """
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
