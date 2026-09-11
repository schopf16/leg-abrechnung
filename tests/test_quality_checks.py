"""Tests for plausibility/consistency checks: Assignment gaps, reading
completeness and metering points with no LEG assigned."""

import uuid
from datetime import date, datetime, timedelta

from app.domain.quality_checks import (
    check_assignment_consistency,
    check_leg_assignment,
    check_leg_upgrade_potential,
    check_onboarding_progress,
    check_reading_completeness,
    check_substation_area_one_sided,
    check_unresolved_bank_transactions,
)
from app.models import bank_transaction as bank_transaction_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import person_onboarding as person_onboarding_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models import assignment as assignment_repo
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.person import Person
from app.models.reading import Reading, upsert_readings
from app.models.site import Site
from app.models.substation_area import SubstationArea
from app.models.assignment import Assignment

YEAR, QUARTER = 2025, 1


def _person(db, name: str = "P") -> int:
    """Create a person and return its id."""
    return person_repo.create(
        db,
        Person(
            id=None, salutation="", company="", first_name=name, last_name="",
            contact_email="", contact_phone="",
            billing_street="", billing_house_number="", billing_postal_code="",
            billing_city="", billing_country="CH",
            iban="", customer_number=None, bkw_customer_number=None, paper_invoice=False, active=True, created_at="",
        ),
    )


def _site(db) -> int:
    """Create a site and return its id."""
    return site_repo.create(
        db,
        Site(
            id=None, street="Musterstrasse", house_number="1", postal_code="3000", municipality="Bern", address_detail="",
            substation_area_id=None, created_at="",
        ),
    )


def _leg(db) -> int:
    """Create a LEG with a unique name and return its id."""
    name = f"Testkreis-{uuid.uuid4().hex[:8]}"
    return leg_repo.create(db, Leg(id=None, name=name, note="", created_at=""))


def _metering_point(db, designation: str, site_id: int, leg_id: int | None = None) -> int:
    """Create a "consumption" MeteringPoint and return its id."""
    return metering_point_repo.create(
        db,
        MeteringPoint(
            id=None, designation=designation,
            direction=DIRECTION_CONSUMPTION, site_id=site_id, leg_id=leg_id,
            pv_capacity_kwp=None, battery_capacity_kwh=None, created_at="",
        ),
    )


def test_check_assignment_consistency_reports_gaps_across_all_metering_points(db):
    """A gap in one MeteringPoint's assignment history is surfaced by the aggregate check."""
    person_id = _person(db)
    site_id = _site(db)
    metering_point_id = _metering_point(db, "CH-Q1", site_id)
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_id, metering_point_id=metering_point_id,
            valid_from=date(2025, 1, 1), valid_to=date(2025, 1, 10), created_at="",
        ),
    )
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_id, metering_point_id=metering_point_id,
            valid_from=date(2025, 1, 20), valid_to=None, created_at="",
        ),
    )

    warnings = check_assignment_consistency(db)
    assert any(w.category == "assignment_gap" for w in warnings)
    # Links straight to the affected MeteringPoint's detail page.
    assert all(w.link == f"/metering-points/{metering_point_id}" for w in warnings)


def test_check_assignment_consistency_clean_history_has_no_warnings(db):
    """A single open-ended Assignment produces no warnings."""
    person_id = _person(db)
    site_id = _site(db)
    metering_point_id = _metering_point(db, "CH-Q1", site_id)
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_id, metering_point_id=metering_point_id,
            valid_from=date(2025, 1, 1), valid_to=None, created_at="",
        ),
    )
    assert check_assignment_consistency(db) == []


def test_check_reading_completeness_flags_days_with_missing_values(db):
    """A day with fewer than 96 readings, while the MeteringPoint is assigned, is flagged."""
    person_id = _person(db)
    site_id = _site(db)
    metering_point_id = _metering_point(db, "CH-Q1", site_id)
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_id, metering_point_id=metering_point_id,
            valid_from=date(YEAR, 1, 1), valid_to=None, created_at="",
        ),
    )

    # Only 4 of the expected 96 readings for Jan 15th.
    day = datetime(YEAR, 1, 15)
    readings = [
        Reading(metering_point_id=metering_point_id, timestamp=(day + timedelta(minutes=15 * i)).isoformat(), direction="consumption", kwh=0.1, source="test")
        for i in range(4)
    ]
    upsert_readings(db, readings)

    warnings = check_reading_completeness(db, YEAR, QUARTER)
    assert any("2025-01-15" in w.message for w in warnings)
    # Links straight to the affected MeteringPoint's detail page.
    assert all(w.link == f"/metering-points/{metering_point_id}" for w in warnings)


def test_check_reading_completeness_ignores_days_without_assignment(db):
    """A MeteringPoint that was never assigned to anyone produces no completeness warnings."""
    site_id = _site(db)
    _metering_point(db, "CH-Q1", site_id)
    # No Assignment created at all.
    warnings = check_reading_completeness(db, YEAR, QUARTER)
    assert warnings == []


def test_check_reading_completeness_no_warning_for_fully_covered_day(db):
    """A day with exactly 96 readings is not flagged."""
    person_id = _person(db)
    site_id = _site(db)
    metering_point_id = _metering_point(db, "CH-Q1", site_id)
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_id, metering_point_id=metering_point_id,
            valid_from=date(YEAR, 1, 15), valid_to=date(YEAR, 1, 15), created_at="",
        ),
    )
    day = datetime(YEAR, 1, 15)
    readings = [
        Reading(metering_point_id=metering_point_id, timestamp=(day + timedelta(minutes=15 * i)).isoformat(), direction="consumption", kwh=0.1, source="test")
        for i in range(96)
    ]
    upsert_readings(db, readings)

    warnings = check_reading_completeness(db, YEAR, QUARTER)
    assert not any("2025-01-15" in w.message for w in warnings)


def test_check_leg_assignment_no_warnings_when_all_assigned(db):
    """metering points that all have a LEG assigned produce no warnings."""
    leg_a = _leg(db)
    leg_b = _leg(db)
    site = _site(db)
    _metering_point(db, "CH-A", site, leg_id=leg_a)
    _metering_point(db, "CH-B", site, leg_id=leg_b)

    # Multiple different LEGs in use at once is normal, not a warning.
    assert check_leg_assignment(db) == []


def test_check_leg_assignment_flags_unresolved_metering_point(db):
    """A MeteringPoint with no LEG assigned is flagged."""
    leg_id = _leg(db)
    site = _site(db)
    _metering_point(db, "CH-A", site, leg_id=leg_id)
    _metering_point(db, "CH-C", site, leg_id=None)

    warnings = check_leg_assignment(db)
    assert any(w.category == "leg_not_assigned" for w in warnings)
    assert len(warnings) == 1
    # Links straight to the unresolved MeteringPoint's detail page.
    unresolved_id = metering_point_repo.get_by_designation(db, "CH-C").id
    assert warnings[0].link == f"/metering-points/{unresolved_id}"


def test_check_leg_assignment_ignores_other_metering_points_with_leg(db):
    """metering points that already have a LEG don't influence the check."""
    leg_id = _leg(db)
    site = _site(db)
    _metering_point(db, "CH-A", site, leg_id=leg_id)

    assert check_leg_assignment(db) == []


def test_check_onboarding_progress_flags_overdue_step(db):
    """An onboarding stuck for longer than the (default 30-day) threshold is flagged."""
    person_id = _person(db, "Overdue")
    person_onboarding_repo.start_for_person(
        db, person_id, registered_at=date.today() - timedelta(days=40)
    )

    warnings = check_onboarding_progress(db)
    assert any(w.category == "onboarding_overdue" for w in warnings)
    assert "Overdue" in warnings[0].message
    # Links straight to the person's detail page.
    assert warnings[0].link == f"/persons/{person_id}"


def test_check_onboarding_progress_ignores_step_within_threshold(db):
    """An onboarding well within the threshold produces no warning."""
    person_id = _person(db, "OnTrack")
    person_onboarding_repo.start_for_person(
        db, person_id, registered_at=date.today() - timedelta(days=5)
    )

    assert check_onboarding_progress(db) == []


def test_check_onboarding_progress_ignores_completed_onboarding(db):
    """A fully completed onboarding is never flagged, however old it is."""
    person_id = _person(db, "Done")
    onboarding = person_onboarding_repo.start_for_person(
        db, person_id, registered_at=date.today() - timedelta(days=100)
    )
    onboarding.leg_assigned_at = date.today() - timedelta(days=90)
    onboarding.contract_signed_at = date.today() - timedelta(days=80)
    onboarding.bkw_registered_at = date.today() - timedelta(days=70)
    onboarding.bkw_confirmed_at = date.today() - timedelta(days=60)
    person_onboarding_repo.update(db, onboarding)

    assert check_onboarding_progress(db) == []


def test_check_onboarding_progress_respects_configurable_threshold(db):
    """A lowered threshold flags an onboarding that the default would not."""
    person_id = _person(db, "Custom")
    person_onboarding_repo.start_for_person(
        db, person_id, registered_at=date.today() - timedelta(days=10)
    )
    assert check_onboarding_progress(db) == []  # still fine at the default 30 days

    settings = settings_repo.get_settings(db)
    settings.onboarding_overdue_days = 5
    settings_repo.update_settings(db, settings)

    warnings = check_onboarding_progress(db)
    assert any(w.category == "onboarding_overdue" for w in warnings)


def test_check_onboarding_progress_ignores_person_without_tracker(db):
    """A person never routed through the onboarding pipeline is never flagged."""
    _person(db, "NoTracker")
    assert check_onboarding_progress(db) == []


def test_check_unresolved_bank_transactions_no_warning_when_none_open(db):
    assert check_unresolved_bank_transactions(db) == []


def test_check_unresolved_bank_transactions_aggregates_into_one_warning(db):
    batch_id = bank_transaction_repo.create_batch(
        db, filename="x.xml", account_iban="", statement_from=None, statement_to=None, entry_count=2
    )
    bank_transaction_repo.insert_transaction(
        db, bank_import_batch_id=batch_id, bank_reference="R1", booking_date="2026-01-01",
        amount_rappen=1000, currency="CHF", credit_debit_indicator="CRDT",
        counterparty_name="A", counterparty_iban="", structured_reference="", remittance_text="",
        source_format="camt053", is_reversal=False,
    )
    bank_transaction_repo.insert_transaction(
        db, bank_import_batch_id=batch_id, bank_reference="R2", booking_date="2026-01-02",
        amount_rappen=2000, currency="CHF", credit_debit_indicator="CRDT",
        counterparty_name="B", counterparty_iban="", structured_reference="", remittance_text="",
        source_format="camt053", is_reversal=False,
    )

    warnings = check_unresolved_bank_transactions(db)

    assert len(warnings) == 1
    assert "2" in warnings[0].message
    assert warnings[0].link == "/receivables"


def test_check_unresolved_bank_transactions_ignores_ignored_entries(db):
    batch_id = bank_transaction_repo.create_batch(
        db, filename="x.xml", account_iban="", statement_from=None, statement_to=None, entry_count=1
    )
    tx_id = bank_transaction_repo.insert_transaction(
        db, bank_import_batch_id=batch_id, bank_reference="R1", booking_date="2026-01-01",
        amount_rappen=1000, currency="CHF", credit_debit_indicator="CRDT",
        counterparty_name="A", counterparty_iban="", structured_reference="", remittance_text="",
        source_format="camt053", is_reversal=False,
    )
    bank_transaction_repo.set_status(db, tx_id, "ignored")

    assert check_unresolved_bank_transactions(db) == []


def _substation_area(db, name: str) -> int:
    """Create a substation area and return its id."""
    return substation_area_repo.create(db, SubstationArea(id=None, name=name, bkw_designation="", note="", created_at=""))


def _site_in(db, substation_area_id: int, *, street: str = "Weg") -> int:
    """Create a site assigned to a substation area and return its id."""
    return site_repo.create(
        db,
        Site(
            id=None, street=street, house_number="1", postal_code="3000", municipality="Bern", address_detail="",
            substation_area_id=substation_area_id, created_at="",
        ),
    )


def _metering_point_direction(
    db, designation: str, site_id: int, direction: str, *, leg_id: int | None = None,
) -> int:
    """Create a MeteringPoint with an explicit direction and return its id."""
    return metering_point_repo.create(
        db,
        MeteringPoint(
            id=None, designation=designation, direction=direction,
            site_id=site_id, leg_id=leg_id, pv_capacity_kwp=None,
            battery_capacity_kwh=None, created_at="",
        ),
    )


def test_check_leg_upgrade_potential_flags_mixed_leg_with_now_workable_substation_area(db):
    """Below `LegSettings.leg_founding_min_persons` (default 7), a
    non-one-sided substation area with only 2 people is not flagged yet -- see
    `test_check_leg_upgrade_potential_respects_configurable_min_persons`."""
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    site_id = _site_in(db, substation_area_id)
    other_site_id = _site_in(db, other_substation_area_id, street="Anderswo")
    mixed_leg_id = _leg(db)

    person_id = _person(db)
    consumption_id = _metering_point_direction(db, "CH1", site_id, DIRECTION_CONSUMPTION, leg_id=mixed_leg_id)
    feed_in_id = _metering_point_direction(db, "CH2", site_id, DIRECTION_FEED_IN, leg_id=mixed_leg_id)
    other_person_id = _person(db, "Andere")
    other_mp_id = _metering_point_direction(db, "CH3", other_site_id, DIRECTION_CONSUMPTION, leg_id=mixed_leg_id)
    for pid, mp_id in ((person_id, consumption_id), (person_id, feed_in_id), (other_person_id, other_mp_id)):
        assignment_repo.create(
            db, Assignment(id=None, person_id=pid, metering_point_id=mp_id, valid_from=date(2026, 1, 1), valid_to=None, created_at="")
        )

    assert check_leg_upgrade_potential(db) == []  # only 2 people at TK1, below the default of 7

    settings = settings_repo.get_settings(db)
    settings.leg_founding_min_persons = 2
    settings_repo.update_settings(db, settings)

    warnings = check_leg_upgrade_potential(db)

    assert len(warnings) == 1
    assert warnings[0].link == "/substation-areas"


def test_check_leg_upgrade_potential_respects_configurable_min_persons(db):
    """Lowering the threshold below the default flags a substation area that
    the default 7 would leave unflagged; raising it above 2 hides it
    again -- both directions of `LegSettings.leg_founding_min_persons`."""
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    site_id = _site_in(db, substation_area_id)
    other_site_id = _site_in(db, other_substation_area_id, street="Anderswo")
    mixed_leg_id = _leg(db)

    person_id = _person(db)
    consumption_id = _metering_point_direction(db, "CH1", site_id, DIRECTION_CONSUMPTION, leg_id=mixed_leg_id)
    feed_in_id = _metering_point_direction(db, "CH2", site_id, DIRECTION_FEED_IN, leg_id=mixed_leg_id)
    other_person_id = _person(db, "Andere")
    other_mp_id = _metering_point_direction(db, "CH3", other_site_id, DIRECTION_CONSUMPTION, leg_id=mixed_leg_id)
    for pid, mp_id in ((person_id, consumption_id), (person_id, feed_in_id), (other_person_id, other_mp_id)):
        assignment_repo.create(
            db, Assignment(id=None, person_id=pid, metering_point_id=mp_id, valid_from=date(2026, 1, 1), valid_to=None, created_at="")
        )

    settings = settings_repo.get_settings(db)
    settings.leg_founding_min_persons = 2
    settings_repo.update_settings(db, settings)
    assert len(check_leg_upgrade_potential(db)) == 1

    settings.leg_founding_min_persons = 3
    settings_repo.update_settings(db, settings)
    assert check_leg_upgrade_potential(db) == []


def test_check_substation_area_one_sided_flags_producer_only_substation_area(db):
    substation_area_id = _substation_area(db, "TK1")
    site_id = _site_in(db, substation_area_id)
    person_id = _person(db)
    feed_in_id = _metering_point_direction(db, "CH1", site_id, DIRECTION_FEED_IN)
    assignment_repo.create(
        db, Assignment(id=None, person_id=person_id, metering_point_id=feed_in_id, valid_from=date(2026, 1, 1), valid_to=None, created_at="")
    )

    warnings = check_substation_area_one_sided(db)

    assert len(warnings) == 1
    assert "Nur Prosumer" in warnings[0].message
    assert warnings[0].link == "/substation-areas"


def test_check_substation_area_one_sided_no_warning_once_resolved_via_mixed_leg(db):
    """The substation area is still producer-only, but its one MeteringPoint already
    sits in a mixed (multi-substation-area) LEG -- the recommended fix is
    already acted on, so no warning."""
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    site_id = _site_in(db, substation_area_id)
    other_site_id = _site_in(db, other_substation_area_id)
    mixed_leg_id = _leg(db)

    person_id = _person(db)
    feed_in_id = _metering_point_direction(db, "CH1", site_id, DIRECTION_FEED_IN, leg_id=mixed_leg_id)
    other_person_id = _person(db, "Andere")
    other_mp_id = _metering_point_direction(db, "CH2", other_site_id, DIRECTION_CONSUMPTION, leg_id=mixed_leg_id)
    for pid, mp_id in ((person_id, feed_in_id), (other_person_id, other_mp_id)):
        assignment_repo.create(
            db, Assignment(id=None, person_id=pid, metering_point_id=mp_id, valid_from=date(2026, 1, 1), valid_to=None, created_at="")
        )

    assert check_substation_area_one_sided(db) == []
