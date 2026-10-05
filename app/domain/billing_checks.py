"""The gate a quarter has to pass before it may be billed."""

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from app.domain.distribution import LegNotAssignedError, compute_quarter_distribution
from app.domain.period import quarter_bounds
from app.domain.quality_checks import check_assignment_consistency, find_reading_gaps
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo

#: How far the two sides of the local sharing may differ **per
#: participant** before the balance counts as broken, in kWh.
#:
#: The engine splits `S(t)` equally between the consumption and the
#: production side, so before rounding the two totals are identical. Each
#: person's total is then rounded to `KWH_PRECISION` (three decimals)
#: independently, which can be off by up to half a milli-kWh either way --
#: so the tolerance has to grow with the number of participants, exactly
#: as `verify_sum_balance` scales its Rappen tolerance with the number of
#: items. A fixed 0.001 kWh read plausibly and then fired on perfectly
#: sound real data; a control point that cries wolf is worse than none.
BALANCE_TOLERANCE_KWH_PER_PARTICIPANT = 0.001

#: Floor for the tolerance above, so a LEG with a single participant
#: still has room for its own rounding.
BALANCE_TOLERANCE_KWH_MINIMUM = 0.001


@dataclass(frozen=True)
class ControlPoint:
    """One pre-billing check and how it came out."""

    key: str
    label: str
    passed: bool
    detail: str
    affected: list[str] = field(default_factory=list)


def control_points_passed(points: list[ControlPoint]) -> bool:
    """Whether every control point is green."""
    return all(point.passed for point in points)


def _check_legs_assigned(connection: sqlite3.Connection) -> ControlPoint:
    """Check that every metering point belongs to a LEG."""
    # Read from the repo rather than from `check_leg_assignment`'s German
    # sentences: the designations are wanted as data, and picking them
    # back out of a message string would break the moment the wording is
    # touched.
    unassigned = [
        metering_point.designation
        for metering_point in metering_point_repo.list_all(connection)
        if metering_point.leg_id is None
    ]
    return ControlPoint(
        key="legs_assigned",
        label="Jeder Messpunkt gehört zu einer LEG",
        passed=not unassigned,
        detail=(
            "Alle Messpunkte sind einer LEG zugeordnet."
            if not unassigned
            else f"{len(unassigned)} Messpunkt(e) ohne LEG -- die Verteilung bricht damit ab."
        ),
        affected=sorted(unassigned),
    )


def _check_readings_complete(connection: sqlite3.Connection, year: int, quarter: int) -> ControlPoint:
    """Check that every assigned metering point delivered a full quarter."""
    gaps = find_reading_gaps(connection, year, quarter)
    by_metering_point: dict[int, list] = {}
    for gap in gaps:
        by_metering_point.setdefault(gap.metering_point_id, []).append(gap)

    # Which of them delivered *nothing* cannot be read off the gaps: a
    # metering point that stopped four days early produces gap days whose
    # counts are all zero too, because the days it did deliver produce no
    # gap at all. The two need different advice -- one file was never
    # imported, the other arrived short -- so the question is put to the
    # readings directly.
    start, end = quarter_bounds(year, quarter)
    silent_ids = set()
    for metering_point_id in by_metering_point:
        row = connection.execute(
            """
            SELECT 1 FROM readings
            WHERE metering_point_id = ? AND timestamp >= ? AND timestamp < ?
            LIMIT 1
            """,
            (metering_point_id, start.isoformat(), end.isoformat()),
        ).fetchone()
        if row is None:
            silent_ids.add(metering_point_id)

    silent = sorted(
        gaps_for_one[0].designation
        for metering_point_id, gaps_for_one in by_metering_point.items()
        if metering_point_id in silent_ids
    )
    partial = sorted(
        gaps_for_one[0].designation
        for metering_point_id, gaps_for_one in by_metering_point.items()
        if metering_point_id not in silent_ids
    )

    if not gaps:
        detail = "Alle zugeordneten Messpunkte haben vollständige 15-Minuten-Werte."
    else:
        parts = []
        if silent:
            parts.append(f"{len(silent)} ganz ohne Messdaten (Import vergessen?)")
        if partial:
            parts.append(f"{len(partial)} mit unvollständigen Tagen (Lieferung unvollständig?)")
        detail = (
            "Betroffen: "
            + " und ".join(parts)
            + ". Fehlende Werte eines Messpunkts verfälschen auch die Rechnungen aller anderen."
        )

    return ControlPoint(
        key="readings_complete",
        label="Messdaten vollständig",
        passed=not gaps,
        detail=detail,
        affected=silent + partial,
    )


def _check_shared_energy_balanced(connection: sqlite3.Connection, year: int, quarter: int) -> ControlPoint:
    """Check that locally delivered equals locally drawn, per LEG."""
    affected: list[str] = []
    details: list[str] = []
    for leg in leg_repo.list_all(connection):
        try:
            distribution = compute_quarter_distribution(connection, leg.id, year, quarter)
        except LegNotAssignedError:
            # Reported by its own control point; without a resolved LEG
            # for every metering point no distribution can be computed at
            # all, so there is nothing to say here.
            return ControlPoint(
                key="energy_balanced",
                label="Lokal geliefert = lokal bezogen",
                passed=False,
                detail="Nicht prüfbar, solange Messpunkte ohne LEG bestehen.",
            )

        consumed = distribution.total_consumed_local_kwh()
        produced = distribution.total_produced_local_kwh()
        tolerance = max(
            BALANCE_TOLERANCE_KWH_MINIMUM,
            BALANCE_TOLERANCE_KWH_PER_PARTICIPANT * len(distribution.person_results),
        )
        if distribution.unassigned_kwh > 0:
            affected.append(leg.name)
            details.append(f"{leg.name}: {distribution.unassigned_kwh:.3f} kWh ohne zugeordnete Person")
        elif abs(consumed - produced) > tolerance:
            affected.append(leg.name)
            details.append(f"{leg.name}: bezogen {consumed:.3f} kWh, geliefert {produced:.3f} kWh")

    return ControlPoint(
        key="energy_balanced",
        label="Lokal geliefert = lokal bezogen",
        passed=not affected,
        detail=(
            "Die lokal geteilte Energie geht in jeder LEG auf."
            if not affected
            else "; ".join(details)
            + ". Geteilte Energie ohne zugeordnete Person deutet auf eine Lücke in den Zuordnungen."
        ),
        affected=affected,
    )


def _check_assignments_consistent(connection: sqlite3.Connection) -> ControlPoint:
    """Check the assignment history for overlaps and gaps."""
    warnings = check_assignment_consistency(connection)
    overlaps = [w for w in warnings if w.category == "assignment_overlap"]
    gaps = [w for w in warnings if w.category == "assignment_gap"]

    if not warnings:
        detail = "Keine Überlappungen oder Lücken in den Zuordnungen."
    else:
        parts = []
        if overlaps:
            parts.append(f"{len(overlaps)} Überlappung(en) -- doppelte Verrechnung möglich")
        if gaps:
            parts.append(f"{len(gaps)} Lücke(n) -- Energie ohne Empfänger")
        detail = "; ".join(parts) + "."

    return ControlPoint(
        key="assignments_consistent",
        label="Zuordnungen lückenlos und überschneidungsfrei",
        passed=not warnings,
        detail=detail,
        affected=[w.message for w in warnings],
    )


def run_control_points(connection: sqlite3.Connection, year: int, quarter: int) -> list[ControlPoint]:
    """Run every pre-billing check for one quarter."""
    return [
        _check_legs_assigned(connection),
        _check_readings_complete(connection, year, quarter),
        _check_assignments_consistent(connection),
        _check_shared_energy_balanced(connection, year, quarter),
    ]


@dataclass(frozen=True)
class PaperInvoice:
    """One invoice that has to leave the house on paper."""

    person_name: str
    address: str
    leg_name: str
    amount_chf: float
    document_path: Optional[str]


def list_paper_invoices(connection: sqlite3.Connection, year: int, quarter: int) -> list[PaperInvoice]:
    """List every document of a quarter that has to be printed and posted."""
    from app.models import billing_run as billing_run_repo
    from app.models import person as person_repo
    from app.sort_keys import person_name_key

    persons = {p.id: p for p in person_repo.list_all(connection)}
    legs = {leg.id: leg for leg in leg_repo.list_all(connection)}

    rows: list[tuple] = []
    for run in billing_run_repo.list_runs(connection):
        if (run.period_year, run.period_quarter) != (year, quarter):
            continue
        for item in billing_run_repo.list_items(connection, run.id):
            person = persons.get(item.person_id)
            if person is None or not person.paper_invoice:
                continue
            rows.append((person, item, legs.get(run.leg_id)))

    rows.sort(key=lambda row: person_name_key(row[0]))
    return [
        PaperInvoice(
            person_name=person.display_name,
            address=(
                f"{person.billing_street_with_number}, {person.billing_postal_code} {person.billing_city}"
            ),
            leg_name=leg.name if leg else "-",
            amount_chf=item.net_amount_rappen / 100,
            document_path=item.pdf_path,
        )
        for person, item, leg in rows
    ]
