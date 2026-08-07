"""The core 15-minute local-solar distribution engine (project brief, section 5).

Sharing happens **within one LEG at a time**, never across LEGs: two
Messpunkte can only exchange energy if their Standorte belong to the same
LEG (project requirement -- "es soll nicht über alle Messstationen
ausgeglichen werden, sondern nur innerhalb der Trafostation"; a LEG by
default matches one physical Trafokreis, see `app.models.leg`). Every
Messpunkt with readings in the requested quarter must therefore have a
resolved LEG (via its Standort) before this runs at all -- regardless of
which LEG's distribution is actually being computed, since an unassigned
Messpunkt silently never appearing in *any* LEG's run would be a worse,
harder-to-notice failure than a loud one; see `LegNotAssignedError`.

For every 15-minute interval `t`, independently per LEG:

1. `P(t)` = sum of all Einspeisung (feed-in) readings at `t` on that LEG.
2. `C(t)` = sum of all Bezug (consumption) readings at `t` on that LEG.
3. `S(t) = min(P(t), C(t))` -- only energy produced *and* consumed at the
   same instant, on the same LEG, can be shared locally.
4. Each Bezug-Messpunkt's locally-covered share is
   `consumption_m(t) * S(t) / C(t)` (zero if `C(t) == 0`).
5. Each Einspeisung-Messpunkt's locally-delivered share is
   `production_m(t) * S(t) / P(t)` (zero if `P(t) == 0`).

Each Messpunkt's interval share is then attributed to whichever Person was
assigned to it at that exact moment (see `app.models.zuordnung`), so a
mid-quarter move splits a Messpunkt's energy between two Personen
automatically. Moving never changes the Messpunkt, its Standort, or that
Standort's LEG -- only which Person the Zuordnung points at.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.period import months_in_quarter, quarter_bounds
from app.models import messpunkt as messpunkt_repo
from app.models import standort as standort_repo
from app.models import zuordnung as zuordnung_repo
from app.models.messpunkt import MESSRICHTUNG_BEZUG
from app.models.reading import list_readings_in_period

#: Number of decimal places internal kWh totals are rounded to.
KWH_PRECISION = 3


class LegNotAssignedError(Exception):
    """Raised when a Messpunkt with readings in the requested quarter has
    no resolved LEG (via its Standort).

    Local sharing is only ever valid within one LEG -- computing a
    distribution while any Messpunkt's LEG is unknown would risk pooling
    energy between physically/administratively unrelated groups, or
    silently excluding that Messpunkt from every LEG's billing without
    anyone noticing. The caller must assign a LEG to the offending
    Standorte (see the "Standorte" page) before a distribution/billing
    run is possible.
    """


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
    """

    person_id: int
    consumed_local_kwh: float = 0.0
    produced_local_kwh: float = 0.0
    consumed_by_month: dict[int, float] = field(default_factory=dict)
    produced_by_month: dict[int, float] = field(default_factory=dict)


@dataclass
class DistributionResult:
    """Result of running the distribution engine over one quarter, for one LEG.

    Attributes:
        leg_id: The LEG this distribution was computed for.
        year: Calendar year of the billing quarter.
        quarter: Quarter number, 1 to 4.
        person_results: Per-person totals, keyed by person id.
        unassigned_kwh: Locally shared energy that could not be attributed
            to any person because no Zuordnung covered that Messpunkt at
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
        """Sum of locally-sourced consumption across all Personen.

        Returns:
            The total in kWh.
        """
        return sum(r.consumed_local_kwh for r in self.person_results.values())

    def total_produced_local_kwh(self) -> float:
        """Sum of locally-delivered production across all Personen.

        Returns:
            The total in kWh.
        """
        return sum(r.produced_local_kwh for r in self.person_results.values())


def _person_at(
    zuordnungen_by_messpunkt: dict[int, list],
    messpunkt_id: int,
    moment: datetime,
    cache: dict[tuple[int, date], "int | None"],
) -> "int | None":
    """Resolve which Person a Messpunkt belonged to at a given moment.

    Results are cached per `(messpunkt_id, date)` since Zuordnungen only
    ever change at day granularity, which turns what would be one lookup
    per 15-minute interval into one lookup per Messpunkt per day.

    Args:
        zuordnungen_by_messpunkt: Pre-loaded assignments, keyed by
            Messpunkt id.
        messpunkt_id: Messpunkt to resolve.
        moment: Interval timestamp to resolve at.
        cache: Mutable memoization cache, shared across calls for one run.

    Returns:
        The person id valid at that moment, or `None` if no Zuordnung
        covers it (a gap in the assignment history).
    """
    key = (messpunkt_id, moment.date())
    if key in cache:
        return cache[key]

    person_id = None
    for zuordnung in zuordnungen_by_messpunkt.get(messpunkt_id, []):
        if zuordnung.covers(moment):
            person_id = zuordnung.person_id
            break

    cache[key] = person_id
    return person_id


def _load_leg_and_bezeichnung_by_messpunkt(
    connection: sqlite3.Connection,
) -> tuple[dict[int, "int | None"], dict[int, str]]:
    """Build Messpunkt lookups needed to group readings by LEG.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `(leg_id_by_messpunkt, bezeichnung_by_messpunkt)` pair, both
        keyed by Messpunkt id. `leg_id_by_messpunkt` values are `None` for
        a Messpunkt whose Standort has no LEG assigned (or whose Standort
        is missing, which should not normally happen).
    """
    standorte_by_id = {s.id: s for s in standort_repo.list_all(connection)}
    leg_id_by_messpunkt: dict[int, "int | None"] = {}
    bezeichnung_by_messpunkt: dict[int, str] = {}
    for messpunkt in messpunkt_repo.list_all(connection):
        standort = standorte_by_id.get(messpunkt.standort_id)
        leg_id_by_messpunkt[messpunkt.id] = standort.leg_id if standort else None
        bezeichnung_by_messpunkt[messpunkt.id] = messpunkt.messpunkt_bezeichnung
    return leg_id_by_messpunkt, bezeichnung_by_messpunkt


def compute_quarter_distribution(
    connection: sqlite3.Connection, leg_id: int, year: int, quarter: int
) -> DistributionResult:
    """Run the distribution engine over one calendar quarter, for one LEG.

    Args:
        connection: Open SQLite connection.
        leg_id: The LEG to compute local sharing for. Only Messpunkte
            whose Standort belongs to this LEG are considered.
        year: Calendar year of the billing quarter.
        quarter: Quarter number, 1 to 4.

    Returns:
        A `DistributionResult` with per-person totals for the quarter,
        scoped to `leg_id`.

    Raises:
        LegNotAssignedError: If any Messpunkt *anywhere* (not just on this
            LEG) has readings in this quarter but no resolved LEG -- a
            deployment-wide data-hygiene gate, since such a Messpunkt
            would otherwise silently never appear in any LEG's run.
    """
    start, end = quarter_bounds(year, quarter)
    rows = list_readings_in_period(connection, start.isoformat(), end.isoformat())

    leg_id_by_messpunkt, bezeichnung_by_messpunkt = _load_leg_and_bezeichnung_by_messpunkt(
        connection
    )
    missing_messpunkt_ids = sorted(
        {row["messpunkt_id"] for row in rows}
        - {mp_id for mp_id, mp_leg_id in leg_id_by_messpunkt.items() if mp_leg_id is not None}
    )
    if missing_messpunkt_ids:
        bezeichnungen = [
            bezeichnung_by_messpunkt.get(mp_id, f"#{mp_id}") for mp_id in missing_messpunkt_ids
        ]
        raise LegNotAssignedError(
            "Folgende Messpunkte mit Messdaten in diesem Quartal sind noch "
            "keiner LEG zugeordnet: " + ", ".join(bezeichnungen) + ". "
            "Bitte zuerst unter „Standorte“ die LEG zuweisen -- lokale "
            "Verteilung ist nur innerhalb derselben LEG möglich."
        )

    leg_rows = [row for row in rows if leg_id_by_messpunkt[row["messpunkt_id"]] == leg_id]

    zuordnungen_by_messpunkt: dict[int, list] = {}
    for messpunkt_id in {row["messpunkt_id"] for row in leg_rows}:
        zuordnungen_by_messpunkt[messpunkt_id] = zuordnung_repo.list_for_messpunkt(
            connection, messpunkt_id
        )

    result = DistributionResult(leg_id=leg_id, year=year, quarter=quarter)
    person_cache: dict[tuple[int, date], "int | None"] = {}

    result.interval_count = len({row["timestamp"] for row in leg_rows})

    intervals: dict[str, list[sqlite3.Row]] = {}
    for row in leg_rows:
        intervals.setdefault(row["timestamp"], []).append(row)

    for timestamp_text, interval_rows in intervals.items():
        moment = datetime.fromisoformat(timestamp_text)
        production_total = sum(
            r["kwh"] for r in interval_rows if r["messrichtung"] != MESSRICHTUNG_BEZUG
        )
        consumption_total = sum(
            r["kwh"] for r in interval_rows if r["messrichtung"] == MESSRICHTUNG_BEZUG
        )
        shared = min(production_total, consumption_total)

        for row in interval_rows:
            is_consumption = row["messrichtung"] == MESSRICHTUNG_BEZUG
            denominator = consumption_total if is_consumption else production_total
            local_share = row["kwh"] * shared / denominator if denominator > 0 else 0.0
            if local_share == 0.0:
                continue

            person_id = _person_at(
                zuordnungen_by_messpunkt, row["messpunkt_id"], moment, person_cache
            )
            if person_id is None:
                result.unassigned_kwh += local_share
                continue

            person_result = result.person_results.setdefault(
                person_id, PersonQuarterResult(person_id=person_id)
            )
            month = moment.month
            if is_consumption:
                person_result.consumed_local_kwh += local_share
                person_result.consumed_by_month[month] = (
                    person_result.consumed_by_month.get(month, 0.0) + local_share
                )
            else:
                person_result.produced_local_kwh += local_share
                person_result.produced_by_month[month] = (
                    person_result.produced_by_month.get(month, 0.0) + local_share
                )

    quarter_months = [month for _, month in months_in_quarter(year, quarter)]
    for person_result in result.person_results.values():
        # Ensure every month of the quarter has an entry (zero if unused)
        # so billing documents always show a complete monthly breakdown.
        for month in quarter_months:
            person_result.consumed_by_month.setdefault(month, 0.0)
            person_result.produced_by_month.setdefault(month, 0.0)

        person_result.consumed_local_kwh = round(
            person_result.consumed_local_kwh, KWH_PRECISION
        )
        person_result.produced_local_kwh = round(
            person_result.produced_local_kwh, KWH_PRECISION
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
