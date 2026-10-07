"""The filled-in Beitrittserklärung: what goes on page 1, and the pages behind it."""

import tempfile
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from app.db.connection import connection_scope
from app.domain.membership_contract import ContractFields, gather
from app.models import assignment as assignment_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.assignment import Assignment
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.person import Person
from app.models.site import Site
from app.models.substation_area import SubstationArea
from app.pdf.membership_contract import build_contract

def _person(connection, **overrides) -> Person:
    """Create a participant."""
    values = {
        "salutation": "Frau",
        "company": "",
        "first_name": "Anna",
        "last_name": "Muster",
        "contact_email": "anna@example.invalid",
        "contact_phone": "031 000 00 00",
        "billing_street": "Erstweg",
        "billing_house_number": "4",
        "billing_postal_code": "3048",
        "billing_city": "Musterdorf",
        "billing_country": "CH",
        "iban": "",
        "paper_invoice": False,
        "note": "",
        "customer_number": None,
        "bkw_customer_number": None,
        "active": True,
        "created_at": "",
    }
    values.update(overrides)
    person_id = person_repo.create(connection, Person(id=None, **values))
    return person_repo.get(connection, person_id)


def _site(connection, *, designation: str = "TRA9365") -> int:
    """Create a site in a Trafokreis."""
    area = substation_area_repo.create(
        connection,
        SubstationArea(
            id=None,
            name=f"Trafokreis-{designation}",
            bkw_designation=designation,
            note="",
            created_at="",
        ),
    )
    return site_repo.create(
        connection,
        Site(
            id=None,
            street="Erstweg",
            house_number="4",
            postal_code="3048",
            municipality="Musterdorf",
            address_detail="",
            substation_area_id=area,
            created_at="",
        ),
    )


def _meter(connection, site_id: int, person_id: int, *, direction: str, designation: str, **caps) -> int:
    """Create a metering point and assign it to a person."""
    metering_point_id = metering_point_repo.create(
        connection,
        MeteringPoint(
            id=None,
            designation=designation,
            direction=direction,
            site_id=site_id,
            leg_id=None,
            pv_capacity_kwp=caps.get("pv_capacity_kwp"),
            battery_capacity_kwh=caps.get("battery_capacity_kwh"),
            wallbox_capacity_kw=caps.get("wallbox_capacity_kw"),
            created_at="",
            label="",
        ),
    )
    assignment_repo.create(
        connection,
        Assignment(
            id=None,
            metering_point_id=metering_point_id,
            person_id=person_id,
            valid_from=date(2026, 1, 1),
            valid_to=None,
            created_at="",
        ),
    )
    return metering_point_id


# --- What goes on the form ------------------------------------------------


def test_every_field_the_form_asks_for_is_filled_from_the_record():
    """Twelve of the thirteen; the Wallbox is the one that needed a column."""
    with connection_scope() as connection:
        person = _person(connection, iban="CH9300762011623852957")
        site = _site(connection)
        _meter(
            connection,
            site,
            person.id,
            direction=DIRECTION_FEED_IN,
            designation="CH1018000000000000000000002",
            pv_capacity_kwp=12.5,
            battery_capacity_kwh=10,
            wallbox_capacity_kw=11,
        )
        _meter(
            connection,
            site,
            person.id,
            direction=DIRECTION_CONSUMPTION,
            designation="CH1018000000000000000000001",
        )

        fields = gather(connection, person)

    assert fields.names == "Anna Muster"
    assert fields.address == "Erstweg 4"
    assert fields.locality == "3048 Musterdorf"
    assert fields.email == "anna@example.invalid"
    assert fields.phone == "031 000 00 00"
    # Without the "TRA" the form's own line already prints.
    assert fields.substation_area == "9365"
    assert fields.consumption_designations == ["CH1018000000000000000000001"]
    assert fields.feed_in_designations == ["CH1018000000000000000000002"]
    assert fields.iban == "CH9300762011623852957"
    assert fields.pv_capacity == "12,5"
    assert fields.battery_capacity == "10"
    assert fields.wallbox_capacity == "11"


def test_a_couple_is_named_twice_on_one_line():
    """One participant, one form, two signatures."""
    with connection_scope() as connection:
        person = _person(
            connection,
            second_salutation="Herr",
            second_first_name="Beat",
            second_last_name="Beispiel",
            second_contact_email="beat@example.invalid",
        )

        fields = gather(connection, person)

    assert fields.names == "Anna Muster und Beat Beispiel"
    assert "anna@example.invalid" in fields.email
    assert "beat@example.invalid" in fields.email


def test_two_metering_points_per_direction_both_appear():
    """Exactly one participant in the live data holds two of each."""
    with connection_scope() as connection:
        person = _person(connection)
        site = _site(connection)
        for index in (1, 2):
            _meter(
                connection,
                site,
                person.id,
                direction=DIRECTION_CONSUMPTION,
                designation=f"CH101800000000000000000000{index}",
            )
            _meter(
                connection,
                site,
                person.id,
                direction=DIRECTION_FEED_IN,
                designation=f"CH101800000000000000000001{index}",
            )

        fields = gather(connection, person)

    assert len(fields.consumption_designations) == 2
    assert len(fields.feed_in_designations) == 2


def test_the_same_meter_held_across_two_assignments_is_named_once():
    """A move within the same flat produces two assignments for one meter."""
    with connection_scope() as connection:
        person = _person(connection)
        site = _site(connection)
        metering_point_id = _meter(
            connection,
            site,
            person.id,
            direction=DIRECTION_CONSUMPTION,
            designation="CH1018000000000000000000001",
        )
        assignment_repo.create(
            connection,
            Assignment(
                id=None,
                metering_point_id=metering_point_id,
                person_id=person.id,
                valid_from=date(2026, 7, 1),
                valid_to=None,
                created_at="",
            ),
        )

        fields = gather(connection, person)

    assert fields.consumption_designations == ["CH1018000000000000000000001"]


def test_nothing_stored_stays_blank_rather_than_becoming_a_zero():
    """ "No battery" and "a battery of zero kWh" are different statements, and only the second would be..."""
    with connection_scope() as connection:
        person = _person(connection)
        site = _site(connection)
        _meter(
            connection,
            site,
            person.id,
            direction=DIRECTION_FEED_IN,
            designation="CH1018000000000000000000002",
        )

        fields = gather(connection, person)

    assert fields.pv_capacity == ""
    assert fields.battery_capacity == ""
    assert fields.wallbox_capacity == ""
    assert fields.iban == ""
    assert fields.company == ""


def test_a_participant_without_any_meter_still_yields_a_form():
    """Five of the ninety-two have no assignment yet, and the contract is exactly what is sent while..."""
    with connection_scope() as connection:
        person = _person(connection)

        fields = gather(connection, person)

    assert fields.names == "Anna Muster"
    assert fields.consumption_designations == []
    assert fields.substation_area == ""
    assert fields.has_feed_in is False


# --- The document ---------------------------------------------------------


def test_the_bundled_form_is_filled_and_its_contract_pages_are_preserved():
    """The official form supplies page one and the Gesellschaftsvertrag behind it."""
    with connection_scope() as connection:
        person = _person(connection, iban="CH9300762011623852957")
        site = _site(connection)
        _meter(
            connection,
            site,
            person.id,
            direction=DIRECTION_CONSUMPTION,
            designation="CH1018000000000000000000001",
        )
        fields = gather(connection, person)

    target = Path(tempfile.mkdtemp()) / "vertrag.pdf"
    build_contract(fields, None, target)

    reader = PdfReader(str(target))
    filled = reader.get_fields()
    assert len(reader.pages) == 7
    assert filled["vorname"]["/V"] == "Anna"
    assert filled["nachname"]["/V"] == "Muster"
    assert filled["adresse"]["/V"] == "Erstweg 4"
    assert filled["plz"]["/V"] == "3048"
    assert filled["ort"]["/V"] == "Musterdorf"
    assert filled["messpunkt_bezug"]["/V"] == "CH1018000000000000000000001"
    assert filled["iban"]["/V"] == "CH93 0076 2011 6238 5295 7"
    assert filled["ort_datum"]["/V"] == ""


def test_contract_generation_reports_missing_template_fields(tmp_path):
    """An incomplete PDF template fails with the field names that are missing."""
    source = tmp_path / "no-fields.pdf"
    canvas = Canvas(str(source), pagesize=A4)
    canvas.showPage()
    canvas.save()

    with pytest.raises(ValueError, match="Fehlende Formularfelder:.*firma.*messpunkt_bezug"):
        build_contract(ContractFields(), source.read_bytes(), tmp_path / "vertrag.pdf")
