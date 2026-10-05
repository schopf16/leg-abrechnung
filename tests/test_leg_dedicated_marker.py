"""Tests for the marker that replaced the LEG-upgrade recommendation."""

from datetime import date

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain import quality_checks
from app.models import assignment as assignment_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.assignment import Assignment
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.person import Person
from app.models.site import Site
from app.models.substation_area import SubstationArea


def _area(connection, name: str) -> int:
    """Create a substation area."""
    return substation_area_repo.create(
        connection,
        SubstationArea(id=None, name=name, bkw_designation="", note="", created_at=""),
    )


def _leg(connection, name: str) -> int:
    """Create a LEG."""
    return leg_repo.create(
        connection,
        Leg(
            id=None,
            name=name,
            note="",
            created_at="",
            production_capacity_percent=None,
            production_capacity_recorded_at=None,
        ),
    )


def _site(connection, area_id: int, street: str) -> int:
    """Create a site in one substation area."""
    return site_repo.create(
        connection,
        Site(
            id=None,
            street=street,
            house_number="1",
            postal_code="3063",
            municipality="Ittigen",
            address_detail="",
            substation_area_id=area_id,
            created_at="",
        ),
    )


def _meter(connection, site_id: int, leg_id: int, direction: str, suffix: str) -> int:
    """Create one metering point."""
    return metering_point_repo.create(
        connection,
        MeteringPoint(
            id=None,
            designation=f"CH10180000000000000000000{suffix}",
            direction=direction,
            site_id=site_id,
            leg_id=leg_id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
    )


def _person(connection, last_name: str) -> int:
    """Create a person to assign meters to."""
    return person_repo.create(
        connection,
        Person(
            id=None,
            salutation="Frau",
            company="",
            first_name="Anna",
            last_name=last_name,
            contact_email="anna@example.invalid",
            contact_phone="",
            billing_street="Weg",
            billing_house_number="1",
            billing_postal_code="3063",
            billing_city="Ittigen",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
    )


def _pooled_deployment(connection) -> dict:
    """Build a pooled LEG spanning two substation areas, one of which has its own LEG."""
    with_own = _area(connection, "TRA-9369")
    without_own = _area(connection, "TRA-1024")
    pooled = _leg(connection, "LEG-Ittigen-Gemeinde")
    dedicated = _leg(connection, "LEG-Ittigen-TRA9369")

    # The dedicated LEG covers TRA-9369 alone.
    own_site = _site(connection, with_own, "Eigenweg")
    _meter(connection, own_site, dedicated, DIRECTION_FEED_IN, "01")
    _meter(connection, own_site, dedicated, DIRECTION_CONSUMPTION, "02")

    # The pooled LEG holds meters in both substation areas -- including one
    # left behind in TRA-9369, a half-finished migration.
    _meter(connection, own_site, pooled, DIRECTION_CONSUMPTION, "03")
    other_site = _site(connection, without_own, "Poolweg")
    _meter(connection, other_site, pooled, DIRECTION_FEED_IN, "04")
    _meter(connection, other_site, pooled, DIRECTION_CONSUMPTION, "05")

    person_id = _person(connection, "Muster")
    for mp in metering_point_repo.list_all(connection):
        assignment_repo.create(
            connection,
            Assignment(
                id=None,
                person_id=person_id,
                metering_point_id=mp.id,
                valid_from=date(2025, 1, 1),
                valid_to=None,
                created_at="",
            ),
        )
    return {
        "pooled": pooled,
        "dedicated": dedicated,
        "with_own": with_own,
        "without_own": without_own,
    }


def _rows_of(leg_id: int) -> list[dict]:
    """Render one LEG's detail page and return its metering point rows."""
    from app.gui.pages import legs as legs_module

    client = Client(ui.page(f"/probe-leg-{leg_id}")(lambda: None), request=None)
    with client:
        legs_module.leg_detail_page(leg_id)
    tables = [e for e in client.elements.values() if e.__class__.__name__ == "Table"]
    assert tables, "die Detailseite muss eine Messpunkt-Tabelle haben"
    return tables[-1].rows


def _columns_of(leg_id: int) -> list[str]:
    """Render one LEG's detail page and return its table column names."""
    from app.gui.pages import legs as legs_module

    client = Client(ui.page(f"/probe-cols-{leg_id}")(lambda: None), request=None)
    with client:
        legs_module.leg_detail_page(leg_id)
    tables = [e for e in client.elements.values() if e.__class__.__name__ == "Table"]
    return [column["name"] for column in tables[-1].columns]


def test_a_pooled_leg_marks_each_meter_with_or_without_a_dedicated_leg():
    """The whole point: 🟢 names the LEG to switch to, 🟠 means found one first."""
    with connection_scope() as connection:
        ids = _pooled_deployment(connection)

    rows = {row["designation"][-2:]: row for row in _rows_of(ids["pooled"])}

    # The meter left behind in the substation area that already has its own LEG.
    assert rows["03"]["dedicated_leg"] == "LEG-Ittigen-TRA9369"
    # The two in the substation area with no LEG of its own.
    assert rows["04"]["dedicated_leg"] == ""
    assert rows["05"]["dedicated_leg"] == ""
    assert all(row["has_substation_area"] for row in rows.values())


def test_the_column_is_hidden_on_a_single_substation_area_leg():
    """On a dedicated LEG the "own LEG" would be itself -- an empty statement."""
    with connection_scope() as connection:
        ids = _pooled_deployment(connection)

    assert "dedicated_leg" in _columns_of(ids["pooled"])
    assert "dedicated_leg" not in _columns_of(ids["dedicated"])


def test_a_leg_does_not_offer_itself_as_the_destination():
    """A dedicated LEG's own rows must not read "🟢 switch to yourself"."""
    with connection_scope() as connection:
        ids = _pooled_deployment(connection)

    for row in _rows_of(ids["dedicated"]):
        assert row["dedicated_leg"] == "", row


def test_a_site_without_a_substation_area_gets_no_marker():
    """The question does not arise, so neither dot is shown."""
    with connection_scope() as connection:
        pooled = _leg(connection, "LEG-Gemeinde")
        area_id = _area(connection, "TRA-1")
        placed = _site(connection, area_id, "Mitweg")
        _meter(connection, placed, pooled, DIRECTION_FEED_IN, "01")
        other = _leg(connection, "LEG-Zweit")
        second_area = _area(connection, "TRA-2")
        second_site = _site(connection, second_area, "Zweitweg")
        _meter(connection, second_site, pooled, DIRECTION_CONSUMPTION, "02")
        _meter(connection, second_site, other, DIRECTION_CONSUMPTION, "03")
        # A site with no substation area recorded at all.
        loose = site_repo.create(
            connection,
            Site(
                id=None,
                street="Ohneweg",
                house_number="9",
                postal_code="3063",
                municipality="Ittigen",
                address_detail="",
                substation_area_id=None,
                created_at="",
            ),
        )
        _meter(connection, loose, pooled, DIRECTION_CONSUMPTION, "04")

    rows = {row["designation"][-2:]: row for row in _rows_of(pooled)}
    assert rows["04"]["has_substation_area"] is False
    assert rows["04"]["dedicated_leg"] == ""


# --- The advice really is gone -----------------------------------------


def test_the_dashboard_no_longer_recommends_a_leg_change(db):
    """The overview must not carry the suggestion any more."""
    assert not hasattr(quality_checks, "check_leg_upgrade_potential")


def test_the_one_sided_warning_is_deliberately_kept(db):
    """Only the recommendation went, not the factual warning."""
    assert hasattr(quality_checks, "check_substation_area_one_sided")

    area_id = _area(db, "TRA-Einseitig")
    leg_id = _leg(db, "LEG-Einseitig")
    site_id = _site(db, area_id, "Einweg")
    mp = _meter(db, site_id, leg_id, DIRECTION_FEED_IN, "01")
    person_id = _person(db, "Solo")
    assignment_repo.create(
        db,
        Assignment(
            id=None,
            person_id=person_id,
            metering_point_id=mp,
            valid_from=date(2025, 1, 1),
            valid_to=None,
            created_at="",
        ),
    )

    warnings = quality_checks.check_substation_area_one_sided(db)
    assert any("TRA-Einseitig" in w.message for w in warnings)


def test_the_participant_mix_no_longer_exposes_the_recommendation(db):
    """`find_upgrade_candidates` and `leg_should_split` are gone for good."""
    from app.domain import participant_mix

    assert not hasattr(participant_mix, "find_upgrade_candidates")
    assert not hasattr(participant_mix, "leg_should_split")
    assert not hasattr(participant_mix, "UpgradeCandidate")
