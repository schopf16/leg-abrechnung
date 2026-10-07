"""Tests for the dashboard's headline numbers (app.gui.pages.dashboard)."""

from app.gui.pages.dashboard import _load_overview
from app.models import person as person_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import assignment as assignment_repo
from app.models import site as site_repo
from app.models.person import Person
from app.models.site import Site
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_FEED_IN, MeteringPoint
from app.models.assignment import Assignment
from datetime import date


def _person(db, name: str = "Test") -> int:
    """Create a person and return its id."""
    return person_repo.create(
        db,
        Person(
            id=None,
            salutation="",
            company="",
            first_name=name,
            last_name="",
            contact_email=f"{name.lower()}@example.invalid",
            contact_phone="",
            billing_street="",
            billing_house_number="",
            billing_postal_code="",
            billing_city="",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
    )


def _site(db, street: str, house_number: str, municipality: str = "Ittigen") -> int:
    """Create a site and return its id."""
    return site_repo.create(
        db,
        Site(
            id=None,
            street=street,
            house_number=house_number,
            postal_code="3063",
            municipality=municipality,
            address_detail="",
            substation_area_id=None,
            created_at="",
        ),
    )


def test_person_count_ignores_deactivated_persons(db):
    """A person who finished offboarding (and was therefore deactivated, because billing history..."""
    active_id = _person(db, "Aktiv")
    gone_id = _person(db, "Ausgetreten")
    person_repo.set_active(db, gone_id, False)

    counts = _load_overview(db)["counts"]

    assert counts["persons"] == 1
    assert person_repo.get(db, active_id).active is True
    assert person_repo.get(db, gone_id).active is False


def test_person_count_is_zero_when_every_person_is_deactivated(db):
    """The "noch keine Personen erfasst" hint keys off this number, so an all-deactivated database must..."""
    person_id = _person(db)
    person_repo.set_active(db, person_id, False)

    assert _load_overview(db)["counts"]["persons"] == 0


def test_site_count_still_includes_sites_without_persons(db):
    """Sites are physical infrastructure and are never removed along with a person -- they keep..."""
    _site(db, "Fischrain", "68")

    assert _load_overview(db)["counts"]["sites"] == 1


def test_missing_feed_in_iban_appears_in_overview_handlungsbedarf(db):
    """The new person finding must reach the overview as a link to the person list."""
    person_id = _person(db, "Einspeiser")
    site_id = _site(db, "Sonnenweg", "3")
    leg_id = leg_repo.create(db, Leg(id=None, name="Test", note="", created_at=""))
    metering_point_id = metering_point_repo.create(
        db,
        MeteringPoint(
            id=None,
            designation="CH-Feed",
            direction=DIRECTION_FEED_IN,
            site_id=site_id,
            leg_id=leg_id,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            created_at="",
        ),
    )
    assignment_repo.create(
        db,
        Assignment(
            id=None,
            person_id=person_id,
            metering_point_id=metering_point_id,
            valid_from=date.today(),
            valid_to=None,
            created_at="",
        ),
    )

    action_items = _load_overview(db)["action_items"]

    assert any("keine IBAN für Gutschriften" in message for message, _ in action_items), action_items
    assert any(link == f"/persons/{person_id}" for _, link in action_items)
