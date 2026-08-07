"""Plausibility and consistency checks (project brief, section 7).

Covers gaps in the Zuordnung history, missing reading periods, the
invoice/credit-note sum balance (lives in `app.domain.billing.
verify_sum_balance`, re-exposed here for a single import point), and
Standorte with Messpunkte that have no LEG assigned yet.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.domain.period import quarter_bounds
from app.models import messpunkt as messpunkt_repo
from app.models import standort as standort_repo
from app.models import zuordnung as zuordnung_repo

#: Expected number of 15-minute readings per Messpunkt per full calendar day.
_EXPECTED_READINGS_PER_DAY = 96


@dataclass
class QualityWarning:
    """One plausibility issue found in the data, for display in the UI.

    Attributes:
        category: One of "zuordnung_ueberlappung", "zuordnung_luecke",
            "messdaten_luecke" or "leg_nicht_zugeordnet".
        message: Human-readable (German) description.
    """

    category: str
    message: str


def check_assignment_consistency(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Check every Messpunkt's Zuordnung history for overlaps and gaps.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `QualityWarning` for each overlap or gap found, across all
        Messpunkte.
    """
    warnings = []
    for messpunkt in messpunkt_repo.list_all(connection):
        for zuordnung_warning in zuordnung_repo.find_warnings(connection, messpunkt.id):
            category = (
                "zuordnung_ueberlappung"
                if zuordnung_warning.kind == "overlap"
                else "zuordnung_luecke"
            )
            warnings.append(QualityWarning(category=category, message=zuordnung_warning.message))
    return warnings


def check_reading_completeness(
    connection: sqlite3.Connection, year: int, quarter: int
) -> list[QualityWarning]:
    """Find days within a quarter where a Messpunkt has fewer than 96 readings.

    Only checks days on which the Messpunkt was actually assigned to a
    Person (an unassigned Messpunkt with no readings is not a data gap, it
    is simply out of service).

    Args:
        connection: Open SQLite connection.
        year: Calendar year of the quarter to check.
        quarter: Quarter number, 1 to 4.

    Returns:
        A `QualityWarning` per Messpunkt/day combination with an
        unexpected reading count.
    """
    start, end = quarter_bounds(year, quarter)
    warnings = []

    for messpunkt in messpunkt_repo.list_all(connection):
        zuordnungen = zuordnung_repo.list_for_messpunkt(connection, messpunkt.id)
        if not zuordnungen:
            continue

        rows = connection.execute(
            "SELECT timestamp FROM readings WHERE messpunkt_id = ? AND timestamp >= ? AND timestamp < ?",
            (messpunkt.id, start.isoformat(), end.isoformat()),
        ).fetchall()
        counts_by_day: dict[str, int] = {}
        for row in rows:
            day_key = row["timestamp"][:10]
            counts_by_day[day_key] = counts_by_day.get(day_key, 0) + 1

        current_day = start.date()
        end_date = end.date()
        while current_day < end_date:
            moment = datetime.combine(current_day, time())
            if any(z.covers(moment) for z in zuordnungen):
                count = counts_by_day.get(current_day.isoformat(), 0)
                if count != _EXPECTED_READINGS_PER_DAY:
                    warnings.append(
                        QualityWarning(
                            category="messdaten_luecke",
                            message=(
                                f"Messpunkt {messpunkt.messpunkt_bezeichnung}: "
                                f"{current_day.isoformat()} hat "
                                f"{count}/{_EXPECTED_READINGS_PER_DAY} Messwerten."
                            ),
                        )
                    )
            current_day += timedelta(days=1)

    return warnings


def check_leg_assignment(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag Standorte with Messpunkte that have no LEG assigned yet.

    Multiple LEGs coexisting in one deployment is normal (see
    `app.models.leg`), so there is no "everyone should share one LEG"
    check anymore -- only a plain data-hygiene check that every in-use
    Standort actually has a LEG, since `compute_quarter_distribution`
    will otherwise refuse to bill it (see
    `app.domain.distribution.LegNotAssignedError`). Surfacing it here lets
    the administrator catch it during data review, before attempting to
    run a billing.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `QualityWarning` per Standort with at least one Messpunkt but no
        LEG assigned. Empty if every in-use Standort has one.
    """
    warnings: list[QualityWarning] = []
    standorte_by_id = {s.id: s for s in standort_repo.list_all(connection)}
    used_standort_ids = {mp.standort_id for mp in messpunkt_repo.list_all(connection)}

    for standort_id in sorted(used_standort_ids):
        standort = standorte_by_id.get(standort_id)
        if standort is None or standort.leg_id is not None:
            continue
        warnings.append(
            QualityWarning(
                category="leg_nicht_zugeordnet",
                message=f"Standort „{standort.adresse_vollstaendig}“ hat noch keine zugeordnete LEG.",
            )
        )

    return warnings
