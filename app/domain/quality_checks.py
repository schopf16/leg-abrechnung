"""Plausibility and consistency checks (project brief, section 7)."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

from app.domain import participant_mix
from app.domain.address_check import KIND_SITE, find_address_issues
from app.domain.leg_composition import compute_leg_composition
from app.domain.period import quarter_bounds
from app.models import bank_transaction as bank_transaction_repo
from app.models import billing_cycle as billing_cycle_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import cooperative_membership as cooperative_membership_repo
from app.models import person as person_repo
from app.models import person_offboarding as person_offboarding_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.sort_keys import text_key
from app.models import assignment as assignment_repo
from app.domain.production_capacity import (
    REQUIRED_PERCENT,
    STATUS_BELOW,
    STATUS_TIGHT,
    compute_headroom,
    format_factor,
    format_percent,
)

#: Expected number of 15-minute readings per MeteringPoint per full calendar day.
_EXPECTED_READINGS_PER_DAY = 96

#: The kinds of record a finding can be about, and therefore the lists that
#: can mark an entry. Plain strings like `QualityWarning.category`, and the
#: same values the pages pass to `problems_for`.
SUBJECT_METERING_POINT = "metering_point"
SUBJECT_PERSON = "person"
SUBJECT_SITE = "site"
SUBJECT_LEG = "leg"
SUBJECT_SUBSTATION_AREA = "substation_area"


@dataclass
class QualityWarning:
    """One plausibility issue found in the data, for display in the UI."""

    category: str
    message: str
    link: Optional[str] = None
    summary: str = ""
    summary_link: Optional[str] = None
    subject_kind: str = ""
    subject_id: Optional[int] = None


def summarise_warnings(warnings: list[QualityWarning]) -> list[QualityWarning]:
    """Collapse repeated findings so the overview stays readable."""
    order: list[tuple[str, str]] = []
    grouped: dict[tuple[str, str], list[QualityWarning]] = {}
    for warning in warnings:
        key = (warning.category, warning.summary)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(warning)

    collapsed: list[QualityWarning] = []
    for key in order:
        group = grouped[key]
        summary = key[1]
        if len(group) > 1 and summary:
            collapsed.append(
                QualityWarning(
                    category=group[0].category,
                    message=f"{len(group)} {summary}",
                    link=group[0].summary_link or group[0].link,
                )
            )
        else:
            collapsed.extend(group)
    return collapsed


def check_assignment_consistency(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Check every MeteringPoint's Assignment history for overlaps and gaps."""
    warnings = []
    for metering_point in metering_point_repo.list_all(connection):
        for assignment_warning in assignment_repo.find_warnings(connection, metering_point.id):
            category = "assignment_overlap" if assignment_warning.kind == "overlap" else "assignment_gap"
            warnings.append(
                QualityWarning(
                    category=category,
                    message=assignment_warning.message,
                    link=f"/metering-points/{metering_point.id}",
                    subject_kind=SUBJECT_METERING_POINT,
                    subject_id=metering_point.id,
                )
            )
    return warnings


@dataclass(frozen=True)
class ReadingGap:
    """One day on which a metering point reported the wrong number of readings."""

    metering_point_id: int
    designation: str
    day: date
    count: int
    expected: int


def find_reading_gaps(connection: sqlite3.Connection, year: int, quarter: int) -> list[ReadingGap]:
    """Find every metering-point/day in a quarter with an unexpected reading count."""
    start, end = quarter_bounds(year, quarter)
    gaps: list[ReadingGap] = []

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
                    gaps.append(
                        ReadingGap(
                            metering_point_id=metering_point.id,
                            designation=metering_point.designation,
                            day=current_day,
                            count=count,
                            expected=_EXPECTED_READINGS_PER_DAY,
                        )
                    )
            current_day += timedelta(days=1)

    return gaps


def check_reading_completeness(
    connection: sqlite3.Connection, year: int, quarter: int
) -> list[QualityWarning]:
    """Find days within a quarter where a MeteringPoint has fewer than 96 readings."""
    return [
        QualityWarning(
            category="reading_gap",
            message=(
                f"Messpunkt {gap.designation}: "
                f"{gap.day.isoformat()} hat {gap.count}/{gap.expected} Messwerten."
            ),
            summary="Tage mit unvollständigen Messwerten",
            summary_link="/import",
            subject_kind=SUBJECT_METERING_POINT,
            subject_id=gap.metering_point_id,
            link=f"/metering-points/{gap.metering_point_id}",
        )
        for gap in find_reading_gaps(connection, year, quarter)
    ]


def check_open_billing_cycle(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Surface a billing quarter that was started but never finished."""
    threshold_days = settings_repo.get_settings(connection).onboarding_overdue_days
    warnings: list[QualityWarning] = []
    for cycle in billing_cycle_repo.list_in_progress(connection):
        if not cycle.is_overdue(threshold_days):
            continue
        _, step_label = cycle.current_step
        warnings.append(
            QualityWarning(
                category="billing_cycle_open",
                message=(
                    f"Rechnungslauf {cycle.label} hängt seit {cycle.days_open()} "
                    f'Tagen bei Schritt "{step_label}".'
                ),
                link="/billing",
            )
        )
    return warnings


def check_leg_assignment(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag metering points that have no LEG assigned yet."""
    warnings: list[QualityWarning] = []
    for metering_point in metering_point_repo.list_all(connection):
        if metering_point.leg_id is not None:
            continue
        warnings.append(
            QualityWarning(
                category="leg_not_assigned",
                message=f"Messpunkt „{metering_point.designation}“ hat noch keine zugeordnete LEG.",
                link=f"/metering-points/{metering_point.id}",
                summary="Messpunkte ohne LEG",
                summary_link="/metering-points",
                subject_kind=SUBJECT_METERING_POINT,
                subject_id=metering_point.id,
            )
        )

    return warnings


def check_offboarding_completed_but_active(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag people whose offboarding is finished while they are still active."""
    warnings: list[QualityWarning] = []
    for offboarding in person_offboarding_repo.list_all(connection):
        if not offboarding.is_complete:
            continue
        person = person_repo.get(connection, offboarding.person_id)
        if person is None or not person.active:
            continue
        warnings.append(
            QualityWarning(
                category="offboarding_completed_but_active",
                message=(
                    f'Austritt von "{person.display_name}" ist abgeschlossen, '
                    "die Person ist aber noch aktiv -- unter „Austritte“ entfernen "
                    "oder deaktivieren."
                ),
                summary="abgeschlossene Austritte, Person noch aktiv",
                summary_link="/offboardings",
                subject_kind=SUBJECT_PERSON,
                subject_id=person.id,
                link=f"/persons/{person.id}",
            )
        )
    return warnings


def check_feed_in_without_consumption(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag people who feed into the LEG but draw nothing from it."""
    warnings: list[QualityWarning] = []
    roles = participant_mix.compute_participant_roles(connection)
    for person_id, site_id in roles.feed_in_only:
        person = person_repo.get(connection, person_id)
        person_name = person.display_name if person else f"Person #{person_id}"
        site = site_repo.get(connection, site_id)
        where = f" am Standort {site.full_address}" if site is not None else ""
        warnings.append(
            QualityWarning(
                category="feed_in_without_consumption",
                message=(
                    f'"{person_name}" speist{where} in die LEG ein, hat dort '
                    "aber keine Bezugs-Zuordnung -- fehlt der Messpunkt für den Bezug?"
                ),
                summary="Anschlüsse speisen ein, ohne zu beziehen",
                # Personen, not Zuordnungen: that is where the finding is
                # marked, and a summary line that lands on a list with no
                # triangle leaves the reader hunting -- which is exactly
                # what it did.
                summary_link="/persons",
                subject_kind=SUBJECT_PERSON,
                subject_id=person.id if person is not None else None,
                link=f"/persons/{person.id}" if person is not None else None,
            )
        )
    return warnings


def check_cooperative_members_without_shares(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag today's Genossenschaft members holding zero shares."""
    today = date.today()
    warnings: list[QualityWarning] = []
    for membership in cooperative_membership_repo.list_all(connection):
        if membership.shares > 0 or not membership.covers(today):
            continue
        person = person_repo.get(connection, membership.person_id)
        person_name = person.display_name if person else f"Person #{membership.person_id}"
        warnings.append(
            QualityWarning(
                category="cooperative_member_without_shares",
                message=(
                    f'Genossenschaftsmitglied "{person_name}" hat 0 Anteile '
                    "erfasst -- Anzahl auf der Personen-Detailseite nachtragen."
                ),
                summary="Genossenschafter ohne erfasste Anteile",
                summary_link="/persons",
                subject_kind=SUBJECT_PERSON,
                subject_id=person.id if person is not None else None,
                link=f"/persons/{person.id}" if person is not None else None,
            )
        )
    return warnings


def check_onboarding_progress(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag interested persons stuck too long on their current onboarding step."""
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
                summary="Aufnahmen hängen zu lange auf einem Schritt",
                summary_link="/onboardings",
                subject_kind=SUBJECT_PERSON,
                subject_id=person.id if person is not None else None,
            )
        )

    return warnings


def check_unresolved_bank_transactions(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag imported bank statement entries still awaiting a decision."""
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


def _recorded_on(recorded_at: Optional[str]) -> str:
    """Render the date a production percentage was read, for a warning."""
    if not recorded_at:
        return "Stand unbekannt"
    try:
        return f"Stand {date.fromisoformat(recorded_at).strftime('%d.%m.%Y')}"
    except ValueError:
        return f"Stand {recorded_at}"


def _metering_points_added_since(metering_points: list, leg) -> int:
    """Count this LEG's metering points created after its figure was read."""
    if not leg.production_capacity_recorded_at:
        return 0
    return sum(
        1
        for mp in metering_points
        if mp.leg_id == leg.id and (mp.created_at or "")[:10] > leg.production_capacity_recorded_at
    )


def check_leg_production_capacity(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag a LEG whose recorded production capacity is below, or close to, the legal floor."""
    warn_percent = settings_repo.get_settings(connection).production_capacity_warn_percent
    metering_points = metering_point_repo.list_all(connection)
    warnings: list[QualityWarning] = []
    for leg in leg_repo.list_all(connection):
        headroom = compute_headroom(leg.production_capacity_percent, warn_percent=warn_percent)
        stand = _recorded_on(leg.production_capacity_recorded_at)

        # Stale before anything else: acting on a figure that predates the
        # LEG's current shape is worse than acting on a tight one.
        added = _metering_points_added_since(metering_points, leg)
        if leg.production_capacity_percent is not None and added:
            warnings.append(
                QualityWarning(
                    category="leg_production_capacity_stale",
                    message=(
                        f"LEG „{leg.name}“: Produktionsleistung "
                        f"{format_percent(leg.production_capacity_percent)} ({stand}) -- seither "
                        f"{'wurde' if added == 1 else 'wurden'} {added} Messpunkt(e) hinzugefügt. "
                        "Wert im BKW-Portal neu ablesen."
                    ),
                    link="/legs",
                    subject_kind=SUBJECT_LEG,
                    subject_id=leg.id,
                )
            )
        if headroom.status == STATUS_BELOW:
            warnings.append(
                QualityWarning(
                    category="leg_production_capacity_below",
                    message=(
                        f"LEG „{leg.name}“: Produktionsleistung {format_percent(headroom.percent)} liegt "
                        f"unter den gesetzlich nötigen {REQUIRED_PERCENT:.0f} % "
                        f"(Art. 19e Abs. 1 StromVV, {stand}) -- Produktion zubauen oder "
                        "Verbrauchsstellen in eine andere LEG verschieben."
                    ),
                    link="/legs",
                    subject_kind=SUBJECT_LEG,
                    subject_id=leg.id,
                )
            )
        elif headroom.status == STATUS_TIGHT:
            warnings.append(
                QualityWarning(
                    category="leg_production_capacity_tight",
                    message=(
                        f"LEG „{leg.name}“: Produktionsleistung {format_percent(headroom.percent)} -- "
                        f"die gesamte Anschlussleistung der Bezüger darf noch auf das "
                        f"{format_factor(headroom.growth_factor)}-Fache steigen "
                        f"(bei unveränderter Produktion, {stand}). Neue Bezüger besser "
                        "vorerst einer anderen LEG zuweisen."
                    ),
                    link="/legs",
                    subject_kind=SUBJECT_LEG,
                    subject_id=leg.id,
                )
            )
    return warnings


def check_substation_area_one_sided(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Flag substation areas with participants on only one side (nothing to actually share locally)..."""
    warnings: list[QualityWarning] = []
    sites = site_repo.list_all(connection)
    metering_points = metering_point_repo.list_all(connection)
    # text_key, not the repo's `ORDER BY name`: these warnings are read as a
    # list on the dashboard, so they need the same order the Trafokreise page
    # shows -- numbers as numbers, umlauts as their base letter.
    areas = sorted(substation_area_repo.list_all(connection), key=lambda a: text_key(a.name))
    for substation_area in areas:
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
                summary="Trafokreise mit nur einer Richtung",
                summary_link="/substation-areas",
                subject_kind=SUBJECT_SUBSTATION_AREA,
                subject_id=substation_area.id,
            )
        )
    return warnings


def check_addresses(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Report addresses the official register disagrees with."""
    warnings: list[QualityWarning] = []
    for issue in find_address_issues(connection):
        where = "/sites" if issue.kind == KIND_SITE else "/persons"
        warnings.append(
            QualityWarning(
                category="address_not_official",
                message=f"Adresse weicht vom amtlichen Verzeichnis ab: {issue.label}",
                link=where,
                summary=(
                    "Standort-Adressen sollten geprüft werden"
                    if issue.kind == KIND_SITE
                    else "Personen-Adressen sollten geprüft werden"
                ),
                summary_link=where,
                subject_kind=(SUBJECT_SITE if issue.kind == KIND_SITE else SUBJECT_PERSON),
                subject_id=issue.object_id,
            )
        )
    return warnings


#: Every check the overview runs, in display order. One list rather than a
#: sequence written out per caller: the list pages mark exactly what the
#: overview complains about, and two enumerations would drift until a list
#: stayed unmarked for a finding the overview was already showing.
#:
#: `find_reading_gaps` is deliberately absent -- it needs a year and a
#: quarter, so it belongs to the billing run rather than to a page load.
ALL_CHECKS = (
    check_assignment_consistency,
    check_leg_assignment,
    check_onboarding_progress,
    check_offboarding_completed_but_active,
    check_cooperative_members_without_shares,
    check_feed_in_without_consumption,
    check_open_billing_cycle,
    check_unresolved_bank_transactions,
    check_substation_area_one_sided,
    check_leg_production_capacity,
    check_addresses,
)

#: Which `SUBJECT_*` each check can mark, so a list can run only the checks
#: that could possibly concern it. Measured on the live deployment, where
#: every list page ran all eleven: `problems_for` took 128 ms, of which
#: `check_substation_area_one_sided` was 49 ms and `check_addresses` 41 ms
#: -- and on the Personen page the first of those cannot produce a single
#: finding. The administrator reported the page opening slowly, and this was
#: most of it.
#:
#: Declared rather than discovered, because a check's subject is only known
#: after running it, which is the thing being avoided. Drift is caught by
#: `test_every_check_declares_the_subjects_it_can_mark`, which re-derives
#: the table from the source rather than from a run -- no test data triggers
#: all eleven checks at once.
CHECK_SUBJECTS: dict = {
    check_assignment_consistency: (SUBJECT_METERING_POINT,),
    check_leg_assignment: (SUBJECT_METERING_POINT,),
    check_onboarding_progress: (SUBJECT_PERSON,),
    check_offboarding_completed_but_active: (SUBJECT_PERSON,),
    check_cooperative_members_without_shares: (SUBJECT_PERSON,),
    check_feed_in_without_consumption: (SUBJECT_PERSON,),
    # Deployment-wide findings: they belong on the overview and mark no
    # single record, so no list has to run them.
    check_open_billing_cycle: (),
    check_unresolved_bank_transactions: (),
    check_substation_area_one_sided: (SUBJECT_SUBSTATION_AREA,),
    check_leg_production_capacity: (SUBJECT_LEG,),
    check_addresses: (SUBJECT_PERSON, SUBJECT_SITE),
}


def checks_for(subject_kind: str) -> tuple:
    """The checks that can mark an entry of one list."""
    return tuple(check for check in ALL_CHECKS if subject_kind in CHECK_SUBJECTS[check])


def all_warnings(connection: sqlite3.Connection) -> list[QualityWarning]:
    """Run every check, in the order the overview shows them."""
    warnings: list[QualityWarning] = []
    for check in ALL_CHECKS:
        warnings.extend(check(connection))
    return warnings


def problems_for(connection: sqlite3.Connection, subject_kind: str) -> dict[int, list[QualityWarning]]:
    """Which entries of one list have something wrong with them."""
    found: dict[int, list[QualityWarning]] = {}
    for check in checks_for(subject_kind):
        for warning in check(connection):
            if warning.subject_kind != subject_kind or warning.subject_id is None:
                continue
            found.setdefault(warning.subject_id, []).append(warning)
    return found
