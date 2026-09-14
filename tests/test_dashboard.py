"""Tests for the dashboard's headline numbers (app.gui.pages.dashboard).

`_load_overview` takes a plain connection and returns a dict, so it is
testable without rendering any NiceGUI page.
"""

from app.gui.pages.dashboard import _load_overview
from app.models import person as person_repo
from app.models import site as site_repo
from app.models.person import Person
from app.models.site import Site


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
    """A person who finished offboarding (and was therefore deactivated,
    because billing history blocked deletion) must not keep inflating the
    headline number -- the Personen page hides them by default too."""
    active_id = _person(db, "Aktiv")
    gone_id = _person(db, "Ausgetreten")
    person_repo.set_active(db, gone_id, False)

    counts = _load_overview(db)["counts"]

    assert counts["persons"] == 1
    assert person_repo.get(db, active_id).active is True
    assert person_repo.get(db, gone_id).active is False


def test_person_count_is_zero_when_every_person_is_deactivated(db):
    """The "noch keine Personen erfasst" hint keys off this number, so an
    all-deactivated database must read as empty rather than as populated."""
    person_id = _person(db)
    person_repo.set_active(db, person_id, False)

    assert _load_overview(db)["counts"]["persons"] == 0


def test_site_count_still_includes_sites_without_persons(db):
    """Sites are physical infrastructure and are never removed along with a
    person -- they keep counting even once nobody is assigned to them."""
    _site(db, "Fischrain", "68")

    assert _load_overview(db)["counts"]["sites"] == 1
