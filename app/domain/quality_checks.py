"""Plausibility and consistency checks (project brief, section 7).

Covers the three checks named in the brief: gaps in the meter-assignment
history, missing reading periods, and the invoice/credit-note sum
balance (the latter lives in `app.domain.billing.verify_sum_balance` and
is simply re-exposed here for a single import point).
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.domain.period import quarter_bounds
from app.models import assignment as assignment_repo
from app.models import meter as meter_repo

#: Expected number of 15-minute readings per meter per full calendar day.
_EXPECTED_READINGS_PER_DAY = 96


@dataclass
class QualityWarning:
    """One plausibility issue found in the data, for display in the UI.

    Attributes:
        category: One of "zuordnung_ueberlappung", "zuordnung_luecke" or
            "messdaten_luecke".
        message: Human-readable (German) description.
    """

    category: str
    message: str


def check_assignment_consistency(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Check every meter's assignment history for overlaps and gaps.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `QualityWarning` for each overlap or gap found, across all meters.
    """
    warnings = []
    for meter in meter_repo.list_all(connection):
        for assignment_warning in assignment_repo.find_warnings(connection, meter.id):
            category = (
                "zuordnung_ueberlappung"
                if assignment_warning.kind == "overlap"
                else "zuordnung_luecke"
            )
            warnings.append(QualityWarning(category=category, message=assignment_warning.message))
    return warnings


def check_reading_completeness(
    connection: sqlite3.Connection, year: int, quarter: int
) -> list[QualityWarning]:
    """Find days within a quarter where a meter has fewer than 96 readings.

    Only checks days on which the meter was actually assigned to a
    participant (an unassigned meter with no readings is not a data gap,
    it is simply out of service).

    Args:
        connection: Open SQLite connection.
        year: Calendar year of the quarter to check.
        quarter: Quarter number, 1 to 4.

    Returns:
        A `QualityWarning` per meter/day combination with an unexpected
        reading count.
    """
    start, end = quarter_bounds(year, quarter)
    warnings = []

    for meter in meter_repo.list_all(connection):
        assignments = assignment_repo.list_for_meter(connection, meter.id)
        if not assignments:
            continue

        rows = connection.execute(
            "SELECT timestamp FROM readings WHERE meter_id = ? AND timestamp >= ? AND timestamp < ?",
            (meter.id, start.isoformat(), end.isoformat()),
        ).fetchall()
        counts_by_day: dict[str, int] = {}
        for row in rows:
            day_key = row["timestamp"][:10]
            counts_by_day[day_key] = counts_by_day.get(day_key, 0) + 1

        current_day = start.date()
        end_date = end.date()
        while current_day < end_date:
            moment = datetime.combine(current_day, time())
            if any(a.covers(moment) for a in assignments):
                count = counts_by_day.get(current_day.isoformat(), 0)
                if count != _EXPECTED_READINGS_PER_DAY:
                    warnings.append(
                        QualityWarning(
                            category="messdaten_luecke",
                            message=(
                                f"Zähler {meter.label}: {current_day.isoformat()} hat "
                                f"{count}/{_EXPECTED_READINGS_PER_DAY} Messwerten."
                            ),
                        )
                    )
            current_day += timedelta(days=1)

    return warnings
