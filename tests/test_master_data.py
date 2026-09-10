"""Tests for Person/MeteringPoint/Assignment/Leg/substation area CRUD, consistency
warnings, and LEG/substation area composition."""

import sqlite3
from datetime import date

import pytest

import app.db.schema as schema_module
from app.db.migrations import MIGRATIONS
from app.domain.leg_composition import compute_leg_composition
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models import assignment as assignment_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, MeteringPoint
from app.models.person import Person
from app.models.site import Site
from app.models.substation_area import SubstationArea
from app.models.assignment import Assignment


def _make_person(name: str = "Test Person") -> Person:
    """Build an unpersisted `Person` for use in tests.

    Args:
        name: Full name to assign, stored entirely in `first_name` (tests
            only ever compare against the combined `full_name`/
            `display_name`, never the individual parts).

    Returns:
        A `Person` with `id=None`.
    """
    return Person(
        id=None,
        salutation="",
        company="",
        first_name=name,
        last_name="",
        contact_email="test@example.ch",
        contact_phone="",
        billing_street="Musterstrasse",
        billing_house_number="1",
        billing_postal_code="3000",
        billing_city="Bern",
        billing_country="CH",
        iban="CH9300762011623852957",
        customer_number=None,
        bkw_customer_number=None,
        paper_invoice=False,
        active=True,
        created_at="",
    )


def _make_metering_point(
    designation: str = "CH1234567890123456789012345",
    direction: str = DIRECTION_CONSUMPTION,
    site_id: int = 1,
    leg_id: int | None = None,
) -> MeteringPoint:
    """Build an unpersisted `MeteringPoint` for use in tests.

    Args:
        designation: Business key to assign.
        direction: Measurement direction.
        site_id: Foreign key of the site the MeteringPoint belongs to.
        leg_id: Foreign key of the assigned LEG, or `None`.

    Returns:
        A `MeteringPoint` with `id=None`.
    """
    return MeteringPoint(
        id=None,
        designation=designation,
        direction=direction,
        site_id=site_id,
        leg_id=leg_id,
        pv_capacity_kwp=None,
        battery_capacity_kwh=None,
        created_at="",
    )


def _make_substation_area(db, name: str = "Bern_TRA00001") -> int:
    """Create a minimal substation area and return its id.

    Args:
        db: Database connection fixture.
        name: Name to assign (must be unique).

    Returns:
        The new substation area's id.
    """
    return substation_area_repo.create(
        db, SubstationArea(id=None, name=name, bkw_designation="", note="", created_at="")
    )


def _make_site(
    db,
    substation_area_id: int | None = None,
    street: str = "Musterstrasse",
    house_number: str = "1",
    postal_code: str = "3000",
) -> int:
    """Create a minimal site and return its id.

    Args:
        db: Database connection fixture.
        substation_area_id: Foreign key of the assigned substation area, or `None`.
        street: Street name.
        house_number: House number.
        plz: Postal code.

    Returns:
        The new site's id.
    """
    return site_repo.create(
        db,
        Site(
            id=None, street=street, house_number=house_number, postal_code=postal_code, municipality="Bern", address_detail="",
            substation_area_id=substation_area_id, created_at="",
        ),
    )


def test_person_crud_roundtrip(db):
    """Creating, fetching, updating and deleting a person all work."""
    person_id = person_repo.create(db, _make_person())
    fetched = person_repo.get(db, person_id)
    assert fetched is not None
    assert fetched.display_name == "Test Person"

    fetched.last_name = "Geändert"
    person_repo.update(db, fetched)
    assert person_repo.get(db, person_id).display_name == "Test Person Geändert"

    person_repo.delete(db, person_id)
    assert person_repo.get(db, person_id) is None


def test_person_display_name_combines_company_and_contact(db):
    """A Person with both Firma and a contact person shows both, company first."""
    person = _make_person("Ansprech Person")
    person.company = "Muster AG"
    person_id = person_repo.create(db, person)

    fetched = person_repo.get(db, person_id)
    assert fetched.display_name == "Muster AG (Ansprech Person)"


def test_person_adressblock_zeilen_includes_salutation_only_with_a_name(db):
    """The recipient address block shows salutation only alongside a personal name."""
    company_only = _make_person("")
    company_only.company = "Nur Firma AG"
    company_only.salutation = "Herr"
    assert company_only.adressblock_zeilen == ["Nur Firma AG"]

    with_contact = _make_person("Max Muster")
    with_contact.company = "Muster AG"
    with_contact.salutation = "Herr"
    assert with_contact.adressblock_zeilen == ["Muster AG", "Herr", "Max Muster"]


def test_person_billing_street_with_number_combines_strasse_and_hausnummer(db):
    """The combined street line omits a missing Strasse or Hausnummer gracefully."""
    person = _make_person("Test")
    person.billing_street = "Musterstrasse"
    person.billing_house_number = "12a"
    assert person.billing_street_with_number == "Musterstrasse 12a"

    person.billing_house_number = ""
    assert person.billing_street_with_number == "Musterstrasse"


def test_person_customer_number_is_auto_assigned_and_unique(db):
    """`create` always assigns a fresh, unique 6-digit customer number."""
    first_id = person_repo.create(db, _make_person("A"))
    second_id = person_repo.create(db, _make_person("B"))

    first = person_repo.get(db, first_id)
    second = person_repo.get(db, second_id)

    assert first.customer_number is not None
    assert second.customer_number is not None
    assert 100_000 <= first.customer_number <= 999_999
    assert first.customer_number != second.customer_number


def test_person_customer_number_ignores_caller_supplied_value(db):
    """`create` always auto-assigns a customer number, ignoring `person.customer_number`."""
    person = _make_person("A")
    person.customer_number = None  # what every caller actually passes for a new Person
    person_id = person_repo.create(db, person)

    fetched = person_repo.get(db, person_id)
    assert fetched.customer_number is not None


def test_person_customer_number_survives_update(db):
    """Updating a person never changes their customer number."""
    person_id = person_repo.create(db, _make_person())
    original = person_repo.get(db, person_id)

    original.first_name = "Neuer Name"
    person_repo.update(db, original)

    assert person_repo.get(db, person_id).customer_number == original.customer_number


def test_person_get_by_email_finds_match(db):
    person_repo.create(db, _make_person())
    found = person_repo.get_by_email(db, "test@example.ch")
    assert found is not None
    assert found.contact_email == "test@example.ch"


def test_person_get_by_email_returns_none_for_unknown_email(db):
    assert person_repo.get_by_email(db, "unknown@example.ch") is None


def test_person_formatted_customer_number_groups_digits(db):
    """`formatted_customer_number` groups the 6 digits as "XXX XXX"."""
    person_id = person_repo.create(db, _make_person())
    person = person_repo.get(db, person_id)
    formatted = person.formatted_customer_number
    digits = f"{person.customer_number:06d}"
    assert formatted == f"{digits[:3]} {digits[3:]}"


def test_migration_21_reassigns_existing_8_digit_customer_number_to_6_digits(monkeypatch):
    """Migration 21 gives every pre-existing Person a fresh, unique 6-digit
    customer number -- simulates a real database that still has old 8-digit
    numbers from before the format change."""
    migrations_before_21 = [m for m in MIGRATIONS if m.version < 21]
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    monkeypatch.setattr(schema_module, "MIGRATIONS", migrations_before_21)
    schema_module.initialize_database(connection)

    # Raw SQL on purpose: at schema version 20 the person table still has
    # its original German column names (renamed to English only in
    # migration 41), so the current person_repo cannot write to it.
    cursor = connection.execute(
        """
        INSERT INTO person (anrede, firma, vorname, nachname, kontakt_email, kontakt_telefon,
                            rechnungsadresse_strasse, rechnungsadresse_hausnummer,
                            rechnungsadresse_plz, rechnungsadresse_ort, rechnungsadresse_land,
                            iban, kundennummer, papierrechnung, aktiv, created_at)
        VALUES ('', '', 'Alt', '', 'test@example.ch', '', 'Musterstrasse', '1', '3000', 'Bern',
                'CH', '', 80083138, 0, 1, '2025-01-01T00:00:00+00:00')
        """
    )
    person_id = cursor.lastrowid
    connection.commit()

    monkeypatch.setattr(schema_module, "MIGRATIONS", MIGRATIONS)
    schema_module.migrate_to_latest(connection)

    migrated = person_repo.get(connection, person_id)
    assert 100_000 <= migrated.customer_number <= 999_999
    assert migrated.customer_number != 80083138


def test_metering_point_rejects_unknown_direction(db):
    """Creating a MeteringPoint with an invalid direction raises ValueError."""
    site_id = _make_site(db)
    with pytest.raises(ValueError):
        metering_point_repo.create(db, _make_metering_point(direction="unbekannt", site_id=site_id))


def test_metering_point_designation_is_unique(db):
    """Two metering points cannot share the same designation."""
    site_id = _make_site(db)
    metering_point_repo.create(db, _make_metering_point(designation="CH1", site_id=site_id))
    with pytest.raises(Exception):
        metering_point_repo.create(db, _make_metering_point(designation="CH1", site_id=site_id))


def test_metering_point_direction_properties():
    """`is_consumption`/`is_feed_in` reflect the MeteringPoint's direction."""
    from app.models.metering_point import DIRECTION_FEED_IN

    consumption = _make_metering_point(direction=DIRECTION_CONSUMPTION)
    feed_in = _make_metering_point(direction=DIRECTION_FEED_IN)
    assert consumption.is_consumption and not consumption.is_feed_in
    assert feed_in.is_feed_in and not feed_in.is_consumption


def test_assignment_covers_respects_open_and_closed_ranges():
    """`Assignment.covers` handles open-ended and bounded periods."""
    open_ended = Assignment(
        id=1, person_id=1, metering_point_id=1,
        valid_from=date(2025, 1, 1), valid_to=None, created_at="",
    )
    assert open_ended.covers(_dt(2025, 6, 1))
    assert not open_ended.covers(_dt(2024, 12, 31))

    bounded = Assignment(
        id=2, person_id=2, metering_point_id=1,
        valid_from=date(2025, 1, 1), valid_to=date(2025, 3, 31), created_at="",
    )
    assert bounded.covers(_dt(2025, 2, 1))
    assert not bounded.covers(_dt(2025, 4, 1))


def test_assignment_is_current_or_upcoming_counts_a_not_yet_started_assignment():
    """Unlike `covers`, a Assignment entered ahead of its start date (e.g.
    next quarter's move-ins prepared in advance) already counts -- only
    one that has actually ended (`valid_to` in the past) does not."""
    future = Assignment(
        id=1, person_id=1, metering_point_id=1,
        valid_from=date(2026, 12, 1), valid_to=None, created_at="",
    )
    assert not future.covers(_dt(2026, 9, 10))
    assert future.is_current_or_upcoming(_dt(2026, 9, 10))

    ended = Assignment(
        id=2, person_id=2, metering_point_id=1,
        valid_from=date(2025, 1, 1), valid_to=date(2025, 3, 31), created_at="",
    )
    assert not ended.is_current_or_upcoming(_dt(2025, 4, 1))


def _dt(year: int, month: int, day: int):
    """Build a naive `datetime` at midnight for the given date.

    Args:
        year: Calendar year.
        month: Calendar month.
        day: Calendar day.

    Returns:
        A `datetime` at 00:00 on the given date.
    """
    from datetime import datetime

    return datetime(year, month, day)


def test_assignment_get_finds_by_id(db):
    """`get` fetches a single Assignment by id, or `None` if unknown."""
    site_id = _make_site(db)
    person_id = person_repo.create(db, _make_person())
    metering_point_id = metering_point_repo.create(db, _make_metering_point(site_id=site_id))
    assignment_id = assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_id, metering_point_id=metering_point_id,
            valid_from=date(2025, 1, 1), valid_to=None, created_at="",
        ),
    )

    found = assignment_repo.get(db, assignment_id)
    assert found is not None
    assert found.person_id == person_id

    assert assignment_repo.get(db, assignment_id + 999) is None


def test_get_relevant_for_metering_point_prefers_the_already_started_one(db):
    """Both an already-started and a not-yet-started Assignment exist --
    the already-started one is the "currently assigned" answer."""
    site_id = _make_site(db)
    person_a = person_repo.create(db, _make_person("Anna"))
    person_b = person_repo.create(db, _make_person("Beat"))
    metering_point_id = metering_point_repo.create(db, _make_metering_point(site_id=site_id))
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_a, metering_point_id=metering_point_id,
            valid_from=date(2025, 1, 1), valid_to=date(2026, 8, 31), created_at="",
        ),
    )
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_b, metering_point_id=metering_point_id,
            valid_from=date(2026, 12, 1), valid_to=None, created_at="",
        ),
    )

    found = assignment_repo.get_relevant_for_metering_point(db, metering_point_id, _dt(2026, 6, 1))
    assert found is not None
    assert found.person_id == person_a


def test_get_relevant_for_metering_point_falls_back_to_soonest_upcoming(db):
    """Nothing has started yet -- falls back to the soonest-starting
    upcoming Assignment instead of reporting "unassigned"."""
    site_id = _make_site(db)
    person_a = person_repo.create(db, _make_person("Anna"))
    person_b = person_repo.create(db, _make_person("Beat"))
    metering_point_id = metering_point_repo.create(db, _make_metering_point(site_id=site_id))
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_a, metering_point_id=metering_point_id,
            valid_from=date(2027, 3, 1), valid_to=None, created_at="",
        ),
    )
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_b, metering_point_id=metering_point_id,
            valid_from=date(2026, 12, 1), valid_to=None, created_at="",
        ),
    )

    found = assignment_repo.get_relevant_for_metering_point(db, metering_point_id, _dt(2026, 9, 10))
    assert found is not None
    assert found.person_id == person_b


def test_get_relevant_for_metering_point_ignores_ended_assignment(db):
    """A Assignment that has already ended is not "upcoming" -- an empty
    history (or one with only past assignments) reports `None`."""
    site_id = _make_site(db)
    person_id = person_repo.create(db, _make_person())
    metering_point_id = metering_point_repo.create(db, _make_metering_point(site_id=site_id))
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_id, metering_point_id=metering_point_id,
            valid_from=date(2020, 1, 1), valid_to=date(2020, 12, 31), created_at="",
        ),
    )

    assert assignment_repo.get_relevant_for_metering_point(db, metering_point_id, _dt(2026, 9, 10)) is None


def test_find_warnings_detects_gap(db):
    """A gap between two Assignment periods is reported."""
    site_id = _make_site(db)
    person_a = person_repo.create(db, _make_person("A"))
    person_b = person_repo.create(db, _make_person("B"))
    metering_point_id = metering_point_repo.create(db, _make_metering_point(site_id=site_id))

    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_a, metering_point_id=metering_point_id,
            valid_from=date(2025, 1, 1), valid_to=date(2025, 1, 31), created_at="",
        ),
    )
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_b, metering_point_id=metering_point_id,
            valid_from=date(2025, 2, 5), valid_to=None, created_at="",
        ),
    )

    warnings = assignment_repo.find_warnings(db, metering_point_id)
    assert len(warnings) == 1
    assert warnings[0].kind == "gap"


def test_find_warnings_detects_overlap(db):
    """Overlapping Assignment periods are reported."""
    site_id = _make_site(db)
    person_a = person_repo.create(db, _make_person("A"))
    person_b = person_repo.create(db, _make_person("B"))
    metering_point_id = metering_point_repo.create(db, _make_metering_point(site_id=site_id))

    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_a, metering_point_id=metering_point_id,
            valid_from=date(2025, 1, 1), valid_to=date(2025, 2, 15), created_at="",
        ),
    )
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_b, metering_point_id=metering_point_id,
            valid_from=date(2025, 2, 1), valid_to=None, created_at="",
        ),
    )

    warnings = assignment_repo.find_warnings(db, metering_point_id)
    assert len(warnings) == 1
    assert warnings[0].kind == "overlap"


def test_find_warnings_none_for_consecutive_periods(db):
    """Back-to-back assignments with no gap or overlap raise no warnings."""
    site_id = _make_site(db)
    person_a = person_repo.create(db, _make_person("A"))
    person_b = person_repo.create(db, _make_person("B"))
    metering_point_id = metering_point_repo.create(db, _make_metering_point(site_id=site_id))

    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_a, metering_point_id=metering_point_id,
            valid_from=date(2025, 1, 1), valid_to=date(2025, 8, 15), created_at="",
        ),
    )
    assignment_repo.create(
        db,
        Assignment(
            id=None, person_id=person_b, metering_point_id=metering_point_id,
            valid_from=date(2025, 8, 16), valid_to=None, created_at="",
        ),
    )

    assert assignment_repo.find_warnings(db, metering_point_id) == []


def _make_leg(name: str = "Ittigen_TRA21359") -> Leg:
    """Build an unpersisted `Leg` for use in tests.

    Args:
        name: Name to assign.

    Returns:
        A `Leg` with `id=None`.
    """
    return Leg(id=None, name=name, note="", created_at="")


def test_leg_get_by_name_finds_exact_match(db):
    """`get_by_name` finds a LEG by its exact name."""
    leg_repo.create(db, _make_leg("Ittigen_TRA21359"))

    found = leg_repo.get_by_name(db, "Ittigen_TRA21359")
    assert found is not None
    assert found.name == "Ittigen_TRA21359"


def test_leg_get_by_name_returns_none_for_unknown_name(db):
    """`get_by_name` returns `None` when no LEG has that name."""
    assert leg_repo.get_by_name(db, "Unbekannt_TRA00000") is None


def test_leg_name_is_unique(db):
    """Two LEGs cannot share the same name."""
    leg_repo.create(db, _make_leg("Ittigen_TRA21359"))
    with pytest.raises(Exception):
        leg_repo.create(db, _make_leg("Ittigen_TRA21359"))


def test_substation_area_get_by_name_finds_exact_match(db):
    """`get_by_name` finds a substation area by its exact name."""
    substation_area_repo.create(
        db, SubstationArea(id=None, name="Bern_TRA00001", bkw_designation="", note="", created_at="")
    )

    found = substation_area_repo.get_by_name(db, "Bern_TRA00001")
    assert found is not None
    assert found.name == "Bern_TRA00001"


def test_substation_area_get_by_name_returns_none_for_unknown_name(db):
    """`get_by_name` returns `None` when no substation area has that name."""
    assert substation_area_repo.get_by_name(db, "Unbekannt_TRA00000") is None


def test_substation_area_name_is_unique(db):
    """Two substation areas cannot share the same name."""
    substation_area_repo.create(
        db, SubstationArea(id=None, name="Bern_TRA00001", bkw_designation="", note="", created_at="")
    )
    with pytest.raises(Exception):
        substation_area_repo.create(
            db, SubstationArea(id=None, name="Bern_TRA00001", bkw_designation="", note="", created_at="")
        )


def test_leg_composition_is_not_mixed_when_all_metering_points_share_one_substation_area(db):
    """A LEG whose metering points are all on one substation area is not flagged as mixed."""
    substation_area_id = _make_substation_area(db, "Bern_TRA00001")
    site_a = _make_site(db, substation_area_id)
    site_b = _make_site(db, substation_area_id)
    leg_id = leg_repo.create(db, _make_leg("Bern_TRA00001"))
    metering_point_repo.create(db, _make_metering_point("CH1", site_id=site_a, leg_id=leg_id))
    metering_point_repo.create(db, _make_metering_point("CH2", site_id=site_b, leg_id=leg_id))

    composition = compute_leg_composition(db, leg_id)
    assert not composition.is_mixed
    assert [t.name for t in composition.substation_areas] == ["Bern_TRA00001"]


def test_leg_composition_is_mixed_when_metering_points_span_two_substation_areas(db):
    """A LEG whose metering points span two substation areas is flagged as mixed."""
    substation_area_a = _make_substation_area(db, "Bern_TRA00001")
    substation_area_b = _make_substation_area(db, "Bern_TRA00002")
    site_a = _make_site(db, substation_area_a)
    site_b = _make_site(db, substation_area_b)
    leg_id = leg_repo.create(db, _make_leg("Gemeinsame_LEG"))
    metering_point_repo.create(db, _make_metering_point("CH1", site_id=site_a, leg_id=leg_id))
    metering_point_repo.create(db, _make_metering_point("CH2", site_id=site_b, leg_id=leg_id))

    composition = compute_leg_composition(db, leg_id)
    assert composition.is_mixed
    assert [t.name for t in composition.substation_areas] == ["Bern_TRA00001", "Bern_TRA00002"]


def test_leg_composition_ignores_other_legs_metering_points(db):
    """metering points belonging to a different LEG don't count toward this LEG's composition."""
    substation_area_a = _make_substation_area(db, "Bern_TRA00001")
    substation_area_b = _make_substation_area(db, "Bern_TRA00002")
    site_a = _make_site(db, substation_area_a)
    site_b = _make_site(db, substation_area_b)
    leg_id = leg_repo.create(db, _make_leg("Bern_TRA00001"))
    other_leg_id = leg_repo.create(db, _make_leg("Bern_TRA00002"))
    metering_point_repo.create(db, _make_metering_point("CH1", site_id=site_a, leg_id=leg_id))
    metering_point_repo.create(db, _make_metering_point("CH2", site_id=site_b, leg_id=other_leg_id))

    composition = compute_leg_composition(db, leg_id)
    assert not composition.is_mixed
    assert [t.name for t in composition.substation_areas] == ["Bern_TRA00001"]


def test_site_find_by_address_finds_exact_match(db):
    """`find_by_address` finds a site by address/Hausnummer/PLZ, case-insensitively."""
    _make_site(db, street="Bergstrasse", house_number="3", postal_code="3001")

    found = site_repo.find_by_address(db, "bergstrasse", "3", "3001")
    assert found is not None
    assert found.street == "Bergstrasse"


def test_site_find_by_address_returns_none_for_no_match(db):
    """`find_by_address` returns `None` when no site has that address."""
    _make_site(db, street="Bergstrasse", house_number="3", postal_code="3001")

    assert site_repo.find_by_address(db, "Bergstrasse", "4", "3001") is None


def test_site_list_all_sorts_house_number_numerically(db):
    """House numbers sort numerically (2 before 10), not lexicographically."""
    _make_site(db, street="Bergstrasse", house_number="10", postal_code="3001")
    _make_site(db, street="Bergstrasse", house_number="2", postal_code="3001")
    _make_site(db, street="Bergstrasse", house_number="1", postal_code="3001")

    house_numbers = [s.house_number for s in site_repo.list_all(db)]
    assert house_numbers == ["1", "2", "10"]


def test_metering_point_pv_and_batterie_fields_roundtrip(db):
    """PV-Leistung and Batteriespeicher survive create/update, and default to `None`."""
    site_id = _make_site(db)
    metering_point = _make_metering_point("CH-PV", site_id=site_id)
    metering_point.pv_capacity_kwp = 6.4
    metering_point.battery_capacity_kwh = 10.0
    metering_point_id = metering_point_repo.create(db, metering_point)

    fetched = metering_point_repo.get(db, metering_point_id)
    assert fetched.pv_capacity_kwp == pytest.approx(6.4)
    assert fetched.battery_capacity_kwh == pytest.approx(10.0)

    fetched.pv_capacity_kwp = 9.9
    fetched.battery_capacity_kwh = None
    metering_point_repo.update(db, fetched)

    updated = metering_point_repo.get(db, metering_point_id)
    assert updated.pv_capacity_kwp == pytest.approx(9.9)
    assert updated.battery_capacity_kwh is None


def test_person_delete_deactivates_when_billing_history_exists(db):
    """A Person with a billing_run_items record is deactivated, not deleted (accounting trail)."""
    leg_id = leg_repo.create(db, _make_leg())
    person_id = person_repo.create(db, _make_person())
    run_id = billing_run_repo.create_run(
        db,
        BillingRun(
            id=None, leg_id=leg_id, period_year=2025, period_quarter=1,
            created_at="", price_rp_per_kwh=12.0, status="erstellt", notes="",
        ),
    )
    billing_run_repo.add_items(
        db,
        [
            BillingRunItem(
                id=None, billing_run_id=run_id, person_id=person_id,
                consumed_kwh=10.0, produced_kwh=0.0, price_rp_per_kwh=12.0,
                admin_fee_consumption_rappen=0, paper_invoice_rappen=0,
                net_amount_rappen=120, pdf_path=None, created_at="",
            ),
        ],
    )

    deleted = person_repo.delete(db, person_id)
    assert deleted is False
    # The person and their customer number/history must still exist, just inactive.
    person = person_repo.get(db, person_id)
    assert person is not None
    assert person.active is False

    person_repo.set_active(db, person_id, True)
    assert person_repo.get(db, person_id).active is True


def test_person_delete_succeeds_without_billing_history(db):
    """A Person with no billing history can be deleted normally."""
    person_id = person_repo.create(db, _make_person())
    deleted = person_repo.delete(db, person_id)
    assert deleted is True
    assert person_repo.get(db, person_id) is None
