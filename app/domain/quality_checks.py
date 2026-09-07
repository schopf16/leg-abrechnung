"""Plausibility and consistency checks (project brief, section 7).

Covers gaps in the Zuordnung history, missing reading periods, the
invoice/credit-note sum balance (lives in `app.domain.billing.
verify_sum_balance`, re-exposed here for a single import point),
Messpunkte that have no LEG assigned yet, and interested persons whose
onboarding (`app.models.person_onboarding`) has been stuck on its current
step for too long.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

from app.domain.period import quarter_bounds
from app.models import bank_transaction as bank_transaction_repo
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models import settings as settings_repo
from app.models import zuordnung as zuordnung_repo

#: Expected number of 15-minute readings per Messpunkt per full calendar day.
_EXPECTED_READINGS_PER_DAY = 96


@dataclass
class QualityWarning:
    """One plausibility issue found in the data, for display in the UI.

    Attributes:
        category: One of "zuordnung_ueberlappung", "zuordnung_luecke",
            "messdaten_luecke", "leg_nicht_zugeordnet" or
            "aufnahme_ueberfaellig".
        message: Human-readable (German) description.
        link: Route path to the specific object this warning is about
            (e.g. `/messpunkte/12`), so the UI can jump straight there
            instead of just naming it in text -- `None` if no detail page
            exists for that kind of object, or the specific record could
            not be resolved.
    """

    category: str
    message: str
    link: Optional[str] = None


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
            warnings.append(
                QualityWarning(
                    category=category,
                    message=zuordnung_warning.message,
                    link=f"/messpunkte/{messpunkt.id}",
                )
            )
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
                            link=f"/messpunkte/{messpunkt.id}",
                        )
                    )
            current_day += timedelta(days=1)

    return warnings


def check_leg_assignment(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag Messpunkte that have no LEG assigned yet.

    Multiple LEGs coexisting in one deployment is normal (see
    `app.models.leg`), so there is no "everyone should share one LEG"
    check anymore -- only a plain data-hygiene check that every Messpunkt
    actually has a LEG, since `compute_quarter_distribution` will
    otherwise refuse to bill it (see
    `app.domain.distribution.LegNotAssignedError`). Surfacing it here lets
    the administrator catch it during data review, before attempting to
    run a billing.

    Args:
        connection: Open SQLite connection.

    Returns:
        A `QualityWarning` per Messpunkt with no LEG assigned. Empty if
        every Messpunkt has one.
    """
    warnings: list[QualityWarning] = []
    for messpunkt in messpunkt_repo.list_all(connection):
        if messpunkt.leg_id is not None:
            continue
        warnings.append(
            QualityWarning(
                category="leg_nicht_zugeordnet",
                message=f"Messpunkt „{messpunkt.messpunkt_bezeichnung}“ hat noch keine zugeordnete LEG.",
                link=f"/messpunkte/{messpunkt.id}",
            )
        )

    return warnings


def check_onboarding_progress(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag interested persons stuck too long on their current onboarding step.

    "Too long" is `LegSettings.onboarding_ueberfaellig_tage` days (default
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
    threshold_days = settings_repo.get_settings(connection).onboarding_ueberfaellig_tage
    warnings: list[QualityWarning] = []
    for onboarding in person_onboarding_repo.list_in_progress(connection):
        if not onboarding.is_overdue(threshold_days):
            continue
        person = person_repo.get(connection, onboarding.person_id)
        person_name = person.anzeige_name if person else f"Person #{onboarding.person_id}"
        _, step_label = onboarding.current_step
        warnings.append(
            QualityWarning(
                category="aufnahme_ueberfaellig",
                message=(
                    f'Aufnahme von "{person_name}" hängt seit '
                    f"{onboarding.days_open()} Tagen bei Schritt "
                    f'"{step_label}".'
                ),
                link=f"/personen/{person.id}" if person is not None else None,
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
            category="bank_buchung_ungeklaert",
            message=f"{open_count} Bank-Buchung(en) noch nicht zugeordnet.",
            link="/debitoren",
        )
    ]
