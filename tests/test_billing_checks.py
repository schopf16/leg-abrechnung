"""Tests for the gate a quarter has to pass before it may be billed.

The case these exist for is not a wrong invoice for one person. It is
that a metering point whose readings were never imported enlarges every
other participant's share of each interval, so one forgotten file means
wrong invoices for everyone -- and the figures look entirely normal.
"""

from datetime import date

from app.domain.billing_checks import control_points_passed, list_paper_invoices, run_control_points
from app.domain.demo_data import SUMMER_QUARTER, WINTER_QUARTER, create_demo_data
from app.domain.distribution import compute_quarter_distribution
from app.models import assignment as assignment_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models.assignment import Assignment
from app.models.metering_point import DIRECTION_CONSUMPTION, MeteringPoint
from app.models.site import Site


def _point(points, key):
    """Pick one control point out of the list.

    Args:
        points: Result of `run_control_points`.
        key: The control point's key.

    Returns:
        The matching `ControlPoint`.
    """
    return next(p for p in points if p.key == key)


def test_complete_demo_data_passes_every_control_point(db):
    """The demo data is sound, so nothing may block."""
    create_demo_data(db)
    points = run_control_points(db, *SUMMER_QUARTER)

    assert control_points_passed(points), [p.detail for p in points if not p.passed]
    assert len(points) == 4


def test_a_quarter_without_feed_in_still_passes(db):
    """No feed-in is not a data fault -- it is a quarter with nothing to share.

    The warning about that comes from the quarter overview
    (`QuarterEnergy.note`); the control points are about whether the data
    is trustworthy, and it is.
    """
    create_demo_data(db)
    points = run_control_points(db, *WINTER_QUARTER)

    assert control_points_passed(points), [p.detail for p in points if not p.passed]


def test_a_metering_point_whose_readings_were_never_imported_blocks(db):
    """The forgotten import: an assigned metering point with no data at all.

    It must block, and it must be named -- being told "something is
    missing" without being told what would leave you hunting.
    """
    create_demo_data(db)
    victim = metering_point_repo.list_all(db)[0]
    db.execute("DELETE FROM readings WHERE metering_point_id = ?", (victim.id,))
    db.commit()

    points = run_control_points(db, *SUMMER_QUARTER)
    readings = _point(points, "readings_complete")

    assert not control_points_passed(points)
    assert not readings.passed
    assert victim.designation in readings.affected
    assert "ganz ohne Messdaten" in readings.detail


def test_a_partially_imported_metering_point_blocks_too(db):
    """A day short a few intervals is a patchy delivery, not a clean quarter."""
    create_demo_data(db)
    victim = metering_point_repo.list_all(db)[0]
    db.execute(
        """
        DELETE FROM readings
        WHERE metering_point_id = ? AND timestamp LIKE ? AND timestamp > ?
        """,
        (victim.id, "2025-07-15%", "2025-07-15T20:00:00"),
    )
    db.commit()

    readings = _point(run_control_points(db, *SUMMER_QUARTER), "readings_complete")

    assert not readings.passed
    assert victim.designation in readings.affected
    assert "unvollständigen Tagen" in readings.detail


def test_a_metering_point_without_any_assignment_does_not_block(db):
    """An unassigned metering point with no data is out of service, not a gap."""
    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]
    site_id = site_repo.create(
        db,
        Site(
            id=None,
            street="Leerweg",
            house_number="1",
            postal_code="3000",
            municipality="Bern",
            address_detail="",
            substation_area_id=None,
            created_at="",
        ),
    )
    metering_point_repo.create(
        db,
        MeteringPoint(
            id=None,
            designation="CH1000000000000000000000098",
            direction=DIRECTION_CONSUMPTION,
            site_id=site_id,
            leg_id=leg.id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
    )

    points = run_control_points(db, *SUMMER_QUARTER)

    assert control_points_passed(points), [p.detail for p in points if not p.passed]


def test_a_metering_point_without_a_leg_blocks_and_stops_the_balance_check(db):
    """Without a LEG on every metering point nothing can be computed at all."""
    create_demo_data(db)
    victim = metering_point_repo.list_all(db)[0]
    victim.leg_id = None
    metering_point_repo.update(db, victim)

    points = run_control_points(db, *SUMMER_QUARTER)

    legs = _point(points, "legs_assigned")
    assert not legs.passed
    assert victim.designation in legs.affected
    # The balance cannot be judged while that is true, and says so rather
    # than crashing on LegNotAssignedError.
    balance = _point(points, "energy_balanced")
    assert not balance.passed
    assert "Nicht prüfbar" in balance.detail


def test_a_gap_in_the_assignments_unbalances_the_shared_energy(db):
    """Shared energy with nobody to attribute it to breaks the balance.

    Locally delivered and locally drawn are equal by construction, so a
    difference always means energy was shared but could not be assigned
    -- never that more was produced than used. Surplus in either
    direction is settled with BKW and never reaches this app.
    """
    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]

    # Cut one consumption metering point's assignment short: its readings
    # keep arriving, but from that day on they belong to nobody.
    consumption = next(mp for mp in metering_point_repo.list_all(db) if mp.is_consumption)
    assignment = assignment_repo.list_for_metering_point(db, consumption.id)[0]
    assignment.valid_to = date(2025, 7, 31)
    assignment_repo.update(db, assignment)

    distribution = compute_quarter_distribution(db, leg.id, *SUMMER_QUARTER)
    assert distribution.unassigned_kwh > 0, "Testaufbau muss Energie ohne Empfänger erzeugen"
    assert distribution.total_consumed_local_kwh() != distribution.total_produced_local_kwh()

    balance = _point(run_control_points(db, *SUMMER_QUARTER), "energy_balanced")

    assert not balance.passed
    assert leg.name in balance.affected
    assert "ohne zugeordnete Person" in balance.detail


def test_overlapping_assignments_block(db):
    """Two people billed for the same metering point at once."""
    create_demo_data(db)
    consumption = next(mp for mp in metering_point_repo.list_all(db) if mp.is_consumption)
    other = next(p for p in person_repo.list_all(db))
    assignment_repo.create(
        db,
        Assignment(
            id=None,
            person_id=other.id,
            metering_point_id=consumption.id,
            valid_from=date(2025, 7, 1),
            valid_to=None,
            created_at="",
        ),
    )

    consistency = _point(run_control_points(db, *SUMMER_QUARTER), "assignments_consistent")

    assert not consistency.passed
    assert "Überlappung" in consistency.detail


def test_the_paper_invoice_list_names_who_needs_a_printed_document(db):
    """The email dispatch skips these people silently; something has to say so."""
    from app.domain.billing import create_billing_runs_for_all_legs

    create_demo_data(db)
    create_billing_runs_for_all_legs(db, *SUMMER_QUARTER, export=False)

    invoices = list_paper_invoices(db, *SUMMER_QUARTER)

    paper_people = {p.display_name for p in person_repo.list_all(db) if p.paper_invoice}
    assert paper_people, "die Demodaten müssen jemanden mit Papierrechnung enthalten"
    assert {invoice.person_name for invoice in invoices} == paper_people
    # A credit note goes out on paper exactly like an invoice.
    assert all(invoice.address for invoice in invoices)


def _distribution_with(person_count: int, consumed: float, produced: float, unassigned: float = 0.0):
    """Build a distribution result with a chosen imbalance.

    Args:
        person_count: How many participants it should report.
        consumed: Total locally drawn kWh, spread over the participants.
        produced: Total locally delivered kWh, on one participant.
        unassigned: Shared energy attributed to nobody.

    Returns:
        A `DistributionResult`.
    """
    from app.domain.distribution import DistributionResult, PersonQuarterResult

    result = DistributionResult(leg_id=1, year=2025, quarter=3, unassigned_kwh=unassigned)
    for index in range(person_count):
        result.person_results[index + 1] = PersonQuarterResult(
            person_id=index + 1,
            consumed_local_kwh=consumed / person_count,
            produced_local_kwh=produced if index == 0 else 0.0,
        )
    return result


def test_rounding_across_many_participants_is_not_reported_as_an_imbalance(db, monkeypatch):
    """The balance tolerance has to scale with the number of participants.

    Each person's totals are rounded to three decimals on their own, so
    the two sides can legitimately differ by a fraction of a milli-kWh
    per participant. A fixed tolerance of 0.001 kWh fired on perfectly
    sound real data -- found by running the thing, not by reading it --
    and a control point that cries wolf is worse than none.
    """
    create_demo_data(db)
    tolerable = _distribution_with(person_count=6, consumed=4786.973, produced=4786.971)
    monkeypatch.setattr(
        "app.domain.billing_checks.compute_quarter_distribution",
        lambda *args, **kwargs: tolerable,
    )

    balance = _point(run_control_points(db, *SUMMER_QUARTER), "energy_balanced")

    assert balance.passed, "0.002 kWh über sechs Personen sind Rundung, kein Fehler"


def test_a_real_imbalance_is_still_caught(db, monkeypatch):
    """Scaling the tolerance must not blunt the check itself."""
    create_demo_data(db)
    broken = _distribution_with(person_count=6, consumed=4800.0, produced=4786.971)
    monkeypatch.setattr(
        "app.domain.billing_checks.compute_quarter_distribution",
        lambda *args, **kwargs: broken,
    )

    balance = _point(run_control_points(db, *SUMMER_QUARTER), "energy_balanced")

    assert not balance.passed
    assert "bezogen" in balance.detail


def test_energy_attributed_to_nobody_is_caught_however_small(db, monkeypatch):
    """Unassigned energy is never rounding, so no tolerance applies to it."""
    create_demo_data(db)
    leaking = _distribution_with(person_count=6, consumed=100.0, produced=100.0, unassigned=0.001)
    monkeypatch.setattr(
        "app.domain.billing_checks.compute_quarter_distribution",
        lambda *args, **kwargs: leaking,
    )

    balance = _point(run_control_points(db, *SUMMER_QUARTER), "energy_balanced")

    assert not balance.passed
    assert "ohne zugeordnete Person" in balance.detail


def test_a_partial_delivery_is_not_reported_as_a_forgotten_import(db):
    """The two failure modes need different advice, so they must not be conflated.

    Found while walking the flow by hand: a metering point whose file
    stopped four days early was announced as "ganz ohne Messdaten --
    Import vergessen?", which sends you looking for a file you already
    imported. The gaps alone cannot tell the two apart, because the days
    a partial delivery *did* cover produce no gap entry at all, so every
    gap it has reads as zero.
    """
    create_demo_data(db)
    points = metering_point_repo.list_all(db)
    never_imported, stopped_early = points[0], points[1]

    db.execute("DELETE FROM readings WHERE metering_point_id = ?", (never_imported.id,))
    db.execute(
        "DELETE FROM readings WHERE metering_point_id = ? AND timestamp >= ?",
        (stopped_early.id, "2025-09-27"),
    )
    db.commit()

    readings = _point(run_control_points(db, *SUMMER_QUARTER), "readings_complete")

    assert not readings.passed
    assert {never_imported.designation, stopped_early.designation} <= set(readings.affected)
    assert "1 ganz ohne Messdaten" in readings.detail
    assert "1 mit unvollständigen Tagen" in readings.detail


def test_a_participant_who_joined_mid_quarter_is_not_missing_data(db):
    """Someone who moved in in August owes no readings for July.

    The completeness check only looks at days an assignment actually
    covers, so a legitimate newcomer must not appear alongside the real
    import failures -- otherwise every quarter with a move-in looks
    broken and the gate gets ignored.
    """
    from datetime import datetime, timedelta

    from app.models.reading import Reading, upsert_readings

    create_demo_data(db)
    move_in = date(2025, 8, 15)

    site_id = site_repo.create(
        db,
        Site(
            id=None,
            street="Ahornweg",
            house_number="5",
            postal_code="3000",
            municipality="Bern",
            address_detail="",
            substation_area_id=None,
            created_at="",
        ),
    )
    metering_point_id = metering_point_repo.create(
        db,
        MeteringPoint(
            id=None,
            designation="CH1000000000000000000000050",
            direction=DIRECTION_CONSUMPTION,
            site_id=site_id,
            leg_id=leg_repo.list_all(db)[0].id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
    )
    assignment_repo.create(
        db,
        Assignment(
            id=None,
            person_id=person_repo.list_all(db)[0].id,
            metering_point_id=metering_point_id,
            valid_from=move_in,
            valid_to=None,
            created_at="",
        ),
    )
    moment = datetime.combine(move_in, datetime.min.time())
    readings = []
    while moment < datetime(2025, 10, 1):
        readings.append(
            Reading(
                metering_point_id=metering_point_id,
                timestamp=moment.isoformat(),
                direction=DIRECTION_CONSUMPTION,
                kwh=0.12,
                source="test",
            )
        )
        moment += timedelta(minutes=15)
    upsert_readings(db, readings)

    points = run_control_points(db, *SUMMER_QUARTER)

    assert control_points_passed(points), [p.detail for p in points if not p.passed]
