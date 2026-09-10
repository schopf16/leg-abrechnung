"""Plausibility and consistency checks (project brief, section 7).

Covers gaps in the Assignment history, missing reading periods, the
invoice/credit-note sum balance (lives in `app.domain.billing.
verify_sum_balance`, re-exposed here for a single import point),
metering points that have no LEG assigned yet, interested persons whose
onboarding (`app.models.person_onboarding`) has been stuck on its current
step for too long, and the one-sided-substation area / LEG-upgrade signals from
`app.domain.participant_mix`.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

from app.domain import participant_mix
from app.domain.leg_composition import compute_leg_composition
from app.domain.period import quarter_bounds
from app.models import bank_transaction as bank_transaction_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models import assignment as assignment_repo

#: Expected number of 15-minute readings per MeteringPoint per full calendar day.
_EXPECTED_READINGS_PER_DAY = 96


@dataclass
class QualityWarning:
    """One plausibility issue found in the data, for display in the UI.

    Attributes:
        category: One of "assignment_overlap", "assignment_gap",
            "messdaten_luecke", "leg_nicht_zugeordnet",
            "onboarding_overdue", "bank_transaction_unresolved",
            "trafokreis_wechsel_potential" or "trafokreis_einseitig".
        message: Human-readable (German) description.
        link: Route path to the specific object this warning is about
            (e.g. `/metering_points/12`), so the UI can jump straight there
            instead of just naming it in text -- `None` if no detail page
            exists for that kind of object, or the specific record could
            not be resolved.
    """

    category: str
    message: str
    link: Optional[str] = None


def check_assignment_consistency(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Check every MeteringPoint's Assignment history for overlaps and gaps.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `QualityWarning` for each overlap or gap found, across all
        metering points.
    """
    warnings = []
    for metering_point in metering_point_repo.list_all(connection):
        for assignment_warning in assignment_repo.find_warnings(connection, metering_point.id):
            category = (
                "assignment_overlap"
                if assignment_warning.kind == "overlap"
                else "assignment_gap"
            )
            warnings.append(
                QualityWarning(
                    category=category,
                    message=assignment_warning.message,
                    link=f"/metering-points/{metering_point.id}",
                )
            )
    return warnings


def check_reading_completeness(
    connection: sqlite3.Connection, year: int, quarter: int
) -> list[QualityWarning]:
    """Find days within a quarter where a MeteringPoint has fewer than 96 readings.

    Only checks days on which the MeteringPoint was actually assigned to a
    Person (an unassigned MeteringPoint with no readings is not a data gap, it
    is simply out of service).

    Args:
        connection: Open SQLite connection.
        year: Calendar year of the quarter to check.
        quarter: Quarter number, 1 to 4.

    Returns:
        A `QualityWarning` per MeteringPoint/day combination with an
        unexpected reading count.
    """
    start, end = quarter_bounds(year, quarter)
    warnings = []

    for metering_point in metering_point_repo.list_all(connection):
        assignments = assignment_repo.list_for_metering_point(connection, metering_point.id)
        if not assignments:
            continue

        rows = connection.execute(
            "SELECT timestamp FROM readings WHERE metering_point_id = ? AND timestamp >= ? AND timestamp < ?",
            (metering_point.id, start.isoformat(), end.isoformat()),
        ).fetchall()
        counts_by_day: dict[str, int] = {}
        for row in rows:
            day_key = row["timestamp"][:10]
            counts_by_day[day_key] = counts_by_day.get(day_key, 0) + 1

        current_day = start.date()
        end_date = end.date()
        while current_day < end_date:
            moment = datetime.combine(current_day, time())
            if any(z.covers(moment) for z in assignments):
                count = counts_by_day.get(current_day.isoformat(), 0)
                if count != _EXPECTED_READINGS_PER_DAY:
                    warnings.append(
                        QualityWarning(
                            category="reading_gap",
                            message=(
                                f"Messpunkt {metering_point.designation}: "
                                f"{current_day.isoformat()} hat "
                                f"{count}/{_EXPECTED_READINGS_PER_DAY} Messwerten."
                            ),
                            link=f"/metering-points/{metering_point.id}",
                        )
                    )
            current_day += timedelta(days=1)

    return warnings


def check_leg_assignment(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag metering points that have no LEG assigned yet.

    Multiple LEGs coexisting in one deployment is normal (see
    `app.models.leg`), so there is no "everyone should share one LEG"
    check anymore -- only a plain data-hygiene check that every MeteringPoint
    actually has a LEG, since `compute_quarter_distribution` will
    otherwise refuse to bill it (see
    `app.domain.distribution.LegNotAssignedError`). Surfacing it here lets
    the administrator catch it during data review, before attempting to
    run a billing.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `QualityWarning` per MeteringPoint with no LEG assigned. Empty if
        every MeteringPoint has one.
    """
    warnings: list[QualityWarning] = []
    for metering_point in metering_point_repo.list_all(connection):
        if metering_point.leg_id is not None:
            continue
        warnings.append(
            QualityWarning(
                category="leg_not_assigned",
                message=f"Messpunkt „{metering_point.designation}“ hat noch keine zugeordnete LEG.",
                link=f"/metering-points/{metering_point.id}",
            )
        )

    return warnings


def check_onboarding_progress(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag interested persons stuck too long on their current onboarding step.

    "Too long" is `LegSettings.onboarding_overdue_days` days (default
    30) since the current step (see `PersonOnboarding.current_step`)
    became active -- see `app.models.person_onboarding` for how that
    reference date is derived. Completed onboardings, and persons never
    routed through this pipeline at all (no tracker exists), never
    produce a warning.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `QualityWarning` per overdue onboarding. Empty if none are overdue.
    """
    threshold_days = settings_repo.get_settings(connection).onboarding_overdue_days
    warnings: list[QualityWarning] = []
    for onboarding in person_onboarding_repo.list_in_progress(connection):
        if not onboarding.is_overdue(threshold_days):
            continue
        person = person_repo.get(connection, onboarding.person_id)
        person_name = person.display_name if person else f"Person #{onboarding.person_id}"
        _, step_label = onboarding.current_step
        warnings.append(
            QualityWarning(
                category="onboarding_overdue",
                message=(
                    f'Aufnahme von "{person_name}" hängt seit '
                    f"{onboarding.days_open()} Tagen bei Schritt "
                    f'"{step_label}".'
                ),
                link=f"/persons/{person.id}" if person is not None else None,
            )
        )

    return warnings


def check_unresolved_bank_transactions(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag imported bank statement entries still awaiting a decision.

    A single aggregated warning (not one per entry) -- see
    `app.models.bank_transaction.list_open` for the underlying query --
    to avoid flooding Handlungsbedarf if many entries are open at once.

    Args:
        connection: Open SQLite connection.

    Returns:
        A single-item list with the aggregated warning, or `[]` if
        nothing is open.
    """
    open_count = len(bank_transaction_repo.list_open(connection))
    if open_count == 0:
        return []
    return [
        QualityWarning(
            category="bank_transaction_unresolved",
            message=f"{open_count} Bank-Buchung(en) noch nicht zugeordnet.",
            link="/receivables",
        )
    ]


def check_leg_upgrade_potential(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag substation areas that could now split off into their own, better-
    discounted LEG (see `app.domain.participant_mix.find_upgrade_candidates`).

    Gated on `LegSettings.leg_founding_min_persons` -- a substation area with
    both a Prosumer and a Consumer but too few people overall is not
    flagged, see that setting's docstring.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `QualityWarning` per substation area with newly-viable upgrade potential.
    """
    min_persons = settings_repo.get_settings(connection).leg_founding_min_persons
    warnings: list[QualityWarning] = []
    for candidate in participant_mix.find_upgrade_candidates(connection, min_persons=min_persons):
        leg_names = ", ".join(f"„{leg.name}“" for leg in candidate.mixed_legs)
        warnings.append(
            QualityWarning(
                category="substation_area_upgrade_potential",
                message=(
                    f"Trafokreis „{candidate.substation_area.name}“ hat jetzt sowohl "
                    f"Prosumer als auch Consumer ({candidate.mix.ratio}) -- "
                    f"{candidate.person_count} Person(en) in {leg_names} könnten "
                    "in ein eigenes LEG wechseln."
                ),
                link="/substation-areas",
            )
        )
    return warnings


def check_substation_area_one_sided(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag substation areas with participants on only one side (nothing to
    actually share locally), unless already resolved via a mixed LEG.

    See `app.domain.participant_mix.compute_participant_mix_for_substation_area`.
    A substation area whose metering points are *all* already in a mixed (multi-
    substation area) LEG is not flagged -- the recommended fix is already acted
    on. A substation area with no participants at all yet is not flagged either
    (nothing to warn about).

    Args:
        connection: Open SQLite connection.

    Returns:
        A `QualityWarning` per still-one-sided substation area.
    """
    warnings: list[QualityWarning] = []
    sites = site_repo.list_all(connection)
    metering_points = metering_point_repo.list_all(connection)
    for substation_area in substation_area_repo.list_all(connection):
        mix = participant_mix.compute_participant_mix_for_substation_area(connection, substation_area.id)
        if mix.hint is None:
            continue
        site_ids = {s.id for s in sites if s.substation_area_id == substation_area.id}
        leg_ids_here = {
            mp.leg_id for mp in metering_points if mp.site_id in site_ids and mp.leg_id is not None
        }
        already_resolved = bool(leg_ids_here) and all(
            compute_leg_composition(connection, leg_id).is_mixed for leg_id in leg_ids_here
        )
        if already_resolved:
            continue
        warnings.append(
            QualityWarning(
                category="substation_area_one_sided",
                message=f"Trafokreis „{substation_area.name}“: {mix.hint}",
                link="/substation-areas",
            )
        )
    return warnings
