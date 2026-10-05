"""The filled-in Beitrittserklärung: what goes on page 1, and the pages behind it.

Thirteen fields, twelve of them already in this database -- the thirteenth,
the Wallbox power, is on the metering point since migration 52 for exactly
this. Every one of them was being copied onto paper by hand.

What no test can answer is whether the sheet *reads* correctly, which is why
`app.gui.contract_preview` exists: it builds the document for a real person
so the administrator can look. These tests cover the parts that can be
stated -- the mapping, the two awkward shapes the live data has, and that
nothing is invented where nothing is stored.
"""

import tempfile
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from app.db.connection import connection_scope
from app.domain.membership_contract import gather
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

#: A one-page PDF standing in for the official seven-page form. Built with
#: reportlab rather than carried as a fixture: the repository holds no
#: tracked PDFs (see `.gitignore`), and a form belongs in the database.
_SOURCE_PAGES = 4


def _source_pdf() -> bytes:
    """A stand-in for the stored Gesellschaftsvertrag.

    Returns:
        A `_SOURCE_PAGES`-page PDF's bytes.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    target = Path(tempfile.mkdtemp()) / "source.pdf"
    canvas = Canvas(str(target), pagesize=A4)
    for number in range(_SOURCE_PAGES):
        canvas.drawString(40, 700, f"Vertragsseite {number + 1}")
        canvas.showPage()
    canvas.save()
    return target.read_bytes()


def _person(connection, **overrides) -> Person:
    """Create a participant.

    Args:
        connection: Open SQLite connection.
        **overrides: Fields to set other than the defaults.

    Returns:
        The stored person.
    """
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
    """Create a site in a Trafokreis.

    Args:
        connection: Open SQLite connection.
        designation: The BKW designation of its Trafokreis.

    Returns:
        The site's id.
    """
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
    """Create a metering point and assign it to a person.

    Args:
        connection: Open SQLite connection.
        site_id: Where it sits.
        person_id: Who holds it.
        direction: `DIRECTION_CONSUMPTION` or `DIRECTION_FEED_IN`.
        designation: The Messpunktbezeichnung.
        **caps: `pv_capacity_kwp`, `battery_capacity_kwh`,
            `wallbox_capacity_kw`.

    Returns:
        The metering point's id.
    """
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
    """One participant, one form, two signatures.

    The form has a single "Vorname, Name" line and both partners are
    contract parties -- see CLAUDE.md on the vZEV model.
    """
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
    """Exactly one participant in the live data holds two of each.

    The form has one line per direction; a form that silently drops a meter
    is worse than one that is crowded.
    """
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
    """ "No battery" and "a battery of zero kWh" are different statements,
    and only the second would be a claim this app cannot support."""
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
    """Five of the ninety-two have no assignment yet, and the contract is
    exactly what is sent while that is still true."""
    with connection_scope() as connection:
        person = _person(connection)

        fields = gather(connection, person)

    assert fields.names == "Anna Muster"
    assert fields.consumption_designations == []
    assert fields.substation_area == ""
    assert fields.has_feed_in is False


# --- The document ---------------------------------------------------------


def test_the_document_is_page_one_plus_the_contract():
    """Page 1 is drawn here; the original's first page is replaced by it."""
    with connection_scope() as connection:
        person = _person(connection)
        fields = gather(connection, person)

    target = Path(tempfile.mkdtemp()) / "vertrag.pdf"
    build_contract(fields, _source_pdf(), target)

    reader = PdfReader(str(target))
    assert len(reader.pages) == _SOURCE_PAGES  # 1 drawn + (_SOURCE_PAGES - 1) appended
    assert "Vertragsseite 1" not in reader.pages[0].extract_text()
    assert "Vertragsseite 2" in reader.pages[1].extract_text()


def test_without_a_stored_form_only_the_filled_page_is_written():
    """Still a usable sheet, and the caller says what is missing."""
    with connection_scope() as connection:
        person = _person(connection)
        fields = gather(connection, person)

    target = Path(tempfile.mkdtemp()) / "nur_seite1.pdf"
    build_contract(fields, None, target)

    assert len(PdfReader(str(target)).pages) == 1


def test_page_one_carries_the_forms_own_wording():
    """Page 1 is not a pixel copy of the original, so the labels are what
    make it recognisable as the same form -- copied verbatim."""
    with connection_scope() as connection:
        person = _person(connection)
        fields = gather(connection, person)

    target = Path(tempfile.mkdtemp()) / "wortlaut.pdf"
    build_contract(fields, None, target)
    text = PdfReader(str(target)).pages[0].extract_text()

    for label in (
        "LEG Teilnehmer",
        "Firma:",
        "Vorname, Name:",
        "Adresse:",
        "PLZ / Ort:",
        "E-Mail:",
        "Tel:",
        "Trafokreis TRA",
        "Bezüger",
        "Messpunktnummer Bezug:",
        "Produzent",
        "Messpunktnummer Einspeisung:",
        "Zusätzliche Angaben bei Einspeisung",
        "IBAN-Nr. für Rückvergütung:",
        "Leistung Solaranlage",
        "Batteriespeicher",
        "Wallbox max. Leistung",
        "Ort und Datum",
        "Unterschrift LEG Teilnehmer",
    ):
        assert label in text, label


def test_the_values_reach_the_page():
    """Drawing them is the point; a page of labels would pass every other
    test here."""
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

    target = Path(tempfile.mkdtemp()) / "werte.pdf"
    build_contract(fields, None, target)
    text = PdfReader(str(target)).pages[0].extract_text()

    assert "Anna Muster" in text
    assert "Erstweg 4" in text
    assert "3048 Musterdorf" in text
    assert "9365" in text
    assert "CH1018000000000000000000001" in text
    assert "CH9300762011623852957" in text


def test_ort_datum_and_the_signature_are_left_empty():
    """They come from the participant, with a pen. Filling them in would be
    this app asserting something it cannot know."""
    with connection_scope() as connection:
        person = _person(connection)
        fields = gather(connection, person)

    target = Path(tempfile.mkdtemp()) / "unterschrift.pdf"
    build_contract(fields, None, target)
    text = PdfReader(str(target)).pages[0].extract_text()

    assert "Ort und Datum" in text
    assert date.today().strftime("%d.%m.%Y") not in text
    assert date.today().isoformat() not in text


def test_a_long_value_is_truncated_rather_than_running_off_the_page():
    """reportlab draws past the margin and says nothing, the trap
    `app.pdf.layout` documents for its own table labels."""
    with connection_scope() as connection:
        person = _person(
            connection,
            company="Sehr lange Firmenbezeichnung mit vielen Wörtern AG in Liquidation",
            second_first_name="Beat",
            second_last_name="Beispiel-Muster-von-Langenthal",
        )
        fields = gather(connection, person)

    target = Path(tempfile.mkdtemp()) / "lang.pdf"
    build_contract(fields, None, target)
    text = PdfReader(str(target)).pages[0].extract_text()

    # Shortened with an ellipsis rather than written over the margin.
    assert "…" in text
