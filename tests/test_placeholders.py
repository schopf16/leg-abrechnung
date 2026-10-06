"""The `{trafokreis}` placeholder, and the one placeholder list the mail forms link to."""

from datetime import date, timedelta

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain.substation_area_lookup import bkw_designations_for_person
from app.emailing.templates import (
    ALL_PLACEHOLDERS,
    CONTEXT_PLACEHOLDERS,
    PERSON_PLACEHOLDERS,
    placeholder_examples,
    placeholder_values,
    render_template,
    validate_person_placeholders,
)
from app.formatting import MISSING
from app.gui.placeholder_help import (
    EXAMPLE_LABEL,
    open_placeholder_dialog,
    placeholders_for,
    render_placeholder_help,
)
from app.models import assignment as assignment_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.assignment import Assignment
from app.models.message_template import OCCASION_DUNNING1, OCCASION_INVOICE
from app.models.metering_point import DIRECTION_CONSUMPTION, MeteringPoint
from app.models.person import Person
from app.models.site import Site
from app.models.substation_area import SubstationArea


def _person(connection) -> Person:
    """Create a participant."""
    person_id = person_repo.create(
        connection,
        Person(
            id=None,
            salutation="Frau",
            company="",
            first_name="Anna",
            last_name="Muster",
            contact_email="anna@example.invalid",
            contact_phone="",
            billing_street="Beispielweg",
            billing_house_number="1",
            billing_postal_code="3063",
            billing_city="Beispielhausen",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
    )
    return person_repo.get(connection, person_id)


def _assigned_meter(connection, person_id: int, *, designation: str, valid_to=None) -> None:
    """Put one metering point in a Trafokreis and assign it to a person."""
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
    site = site_repo.create(
        connection,
        Site(
            id=None,
            street="Beispielweg",
            house_number="1",
            postal_code="3063",
            municipality="Beispielhausen",
            address_detail="",
            substation_area_id=area,
            created_at="",
        ),
    )
    metering_point_id = metering_point_repo.create(
        connection,
        MeteringPoint(
            id=None,
            designation=f"CH101800000000000000000{designation[-4:]}",
            direction=DIRECTION_CONSUMPTION,
            site_id=site,
            leg_id=None,
            pv_capacity_kwp=None,
            battery_capacity_kwh=None,
            wallbox_capacity_kw=None,
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
            valid_from=date.today() - timedelta(days=365),
            valid_to=valid_to,
            created_at="",
        ),
    )


# --- The value ------------------------------------------------------------


def test_the_placeholder_is_bkws_own_designation():
    """`{trafokreis}` writes what BKW calls the Trafokreis, not the local label."""
    with connection_scope() as connection:
        person = _person(connection)
        _assigned_meter(connection, person.id, designation="TRA9365")

        values = placeholder_values(connection, person)
        assert values["trafokreis"] == "TRA9365"
        assert render_template("Ihr Trafokreis: {trafokreis}", values) == "Ihr Trafokreis: TRA9365"


def test_several_trafokreise_are_all_named():
    """A participant spread over two areas gets both, in a stable order."""
    with connection_scope() as connection:
        person = _person(connection)
        _assigned_meter(connection, person.id, designation="TRA9365")
        _assigned_meter(connection, person.id, designation="TRA1204")

        assert bkw_designations_for_person(connection, person.id) == "TRA1204, TRA9365"


def test_an_ended_assignment_says_nothing():
    """Nothing is invented: without a running assignment the value stays empty."""
    with connection_scope() as connection:
        person = _person(connection)
        _assigned_meter(
            connection, person.id, designation="TRA9365", valid_to=date.today() - timedelta(days=1)
        )

        assert bkw_designations_for_person(connection, person.id) == ""


def test_the_local_label_stands_in_where_bkws_is_missing():
    """A Trafokreis without a BKW designation falls back to the administrator's name."""
    with connection_scope() as connection:
        person = _person(connection)
        area = substation_area_repo.create(
            connection,
            SubstationArea(id=None, name="Trafokreis Nord", bkw_designation="", note="", created_at=""),
        )
        site = site_repo.create(
            connection,
            Site(
                id=None,
                street="Beispielweg",
                house_number="2",
                postal_code="3063",
                municipality="Beispielhausen",
                address_detail="",
                substation_area_id=area,
                created_at="",
            ),
        )
        metering_point_id = metering_point_repo.create(
            connection,
            MeteringPoint(
                id=None,
                designation="CH1018000000000000000000009",
                direction=DIRECTION_CONSUMPTION,
                site_id=site,
                leg_id=None,
                pv_capacity_kwp=None,
                battery_capacity_kwh=None,
                wallbox_capacity_kw=None,
                created_at="",
                label="",
            ),
        )
        assignment_repo.create(
            connection,
            Assignment(
                id=None,
                metering_point_id=metering_point_id,
                person_id=person.id,
                valid_from=date.today(),
                valid_to=None,
                created_at="",
            ),
        )

        assert bkw_designations_for_person(connection, person.id) == "Trafokreis Nord"


def test_an_empty_trafokreis_is_reported_before_the_send():
    """The validation step names the recipient the placeholder would be empty for."""
    with connection_scope() as connection:
        person = _person(connection)

        problems = validate_person_placeholders("Hallo {trafokreis}", [person], connection)
        assert [(p.id, fields) for p, fields in problems] == [(person.id, ["trafokreis"])]


def test_without_a_connection_only_the_person_fields_are_checked():
    """`validate_person_placeholders` stays usable where no database is at hand."""
    with connection_scope() as connection:
        person = _person(connection)

    assert validate_person_placeholders("Hallo {trafokreis}", [person]) == []


# --- The one list ---------------------------------------------------------


def test_every_placeholder_has_a_resolved_example():
    """The reference list shows a value for every name it offers."""
    for occasion in (None, OCCASION_INVOICE, OCCASION_DUNNING1):
        examples = placeholder_examples(placeholders_for(occasion))
        empty = [name for name, example in examples if not example.strip()]
        assert empty == [], f"ohne Beispiel bei {occasion}: {empty}"


def test_the_list_offers_every_placeholder_that_exists():
    """Nothing can be used in a text without standing in the list."""
    assert set(ALL_PLACEHOLDERS) == {*PERSON_PLACEHOLDERS, *CONTEXT_PLACEHOLDERS}
    assert "trafokreis" in placeholders_for()
    assert "betrag" in placeholders_for(OCCASION_INVOICE)
    assert "neue_frist" in placeholders_for(OCCASION_DUNNING1)
    assert "neue_frist" not in placeholders_for(), "nur die Mahnung kennt eine neue Frist"


def _table_rows(client: Client) -> list[dict]:
    """The rows of the only table the client holds."""
    tables = [element for element in client.elements.values() if element.__class__.__name__ == "Table"]
    assert len(tables) == 1, f"genau eine Tabelle erwartet, nicht {len(tables)}"
    return tables[0].rows


def test_the_dialog_lists_the_placeholders_with_their_examples():
    """Driving the dialog itself, not only the data behind it."""
    client = Client(ui.page("/probe-placeholder-dialog")(lambda: None), request=None)
    with client:
        open_placeholder_dialog(placeholders_for(OCCASION_INVOICE))
        rows = _table_rows(client)

    by_name = {row["placeholder"]: row["example"] for row in rows}
    assert {"{trafokreis}", "{briefanrede}", "{betrag}"} <= set(by_name)
    assert by_name["{trafokreis}"] == "TRA9365"
    assert by_name["{briefanrede}"].startswith("Guten Tag")


def test_the_link_beside_the_fields_opens_that_same_list():
    """The reference is reached by a click, so the click is what is tested."""
    client = Client(ui.page("/probe-placeholder-link")(lambda: None), request=None)
    with client:
        button = render_placeholder_help(OCCASION_DUNNING1)
        for listener in button._event_listeners.values():
            if listener.type == "click":
                listener.handler(None)
                break
        else:
            raise AssertionError("Der Verweis hat keinen Click-Handler")
        rows = _table_rows(client)

    assert "{neue_frist}" in {row["placeholder"] for row in rows}


def _select(client: Client):
    """The dialog's person select."""
    selects = [element for element in client.elements.values() if element.__class__.__name__ == "Select"]
    assert len(selects) == 1, f"genau eine Auswahl erwartet, nicht {len(selects)}"
    return selects[0]


def test_a_real_person_can_be_picked_and_resolves_their_own_values():
    """The point of the select: see what this one person's mail would say."""
    with connection_scope() as connection:
        person = _person(connection)
        _assigned_meter(connection, person.id, designation="TRA1204")

    client = Client(ui.page("/probe-placeholder-person")(lambda: None), request=None)
    with client:
        open_placeholder_dialog(placeholders_for(OCCASION_INVOICE))
        select = _select(client)
        assert select.options[None] == EXAMPLE_LABEL
        assert select.options[person.id] == person.display_name

        select.value = person.id
        rows = _table_rows(client)

    by_name = {row["placeholder"]: row["example"] for row in rows}
    assert by_name["{trafokreis}"] == "TRA1204"
    assert by_name["{nachname}"] == "Muster"
    assert by_name["{betrag}"] == "142.65", "ein Betrag entsteht erst beim Versand"


def test_a_person_without_a_trafokreis_shows_the_gap():
    """What the administrator wanted to check: the placeholder stays empty."""
    with connection_scope() as connection:
        person = _person(connection)

    client = Client(ui.page("/probe-placeholder-gap")(lambda: None), request=None)
    with client:
        open_placeholder_dialog(placeholders_for())
        _select(client).value = person.id
        rows = _table_rows(client)

    by_name = {row["placeholder"]: row["example"] for row in rows}
    assert by_name["{trafokreis}"] == MISSING
    assert by_name["{firma}"] == MISSING, "auch eine leere Firma wird sichtbar"


def test_a_deactivated_person_is_not_offered():
    """Nothing is mailed to them, so they are not a useful example."""
    with connection_scope() as connection:
        person = _person(connection)
        person_repo.set_active(connection, person.id, False)

    client = Client(ui.page("/probe-placeholder-inactive")(lambda: None), request=None)
    with client:
        open_placeholder_dialog(placeholders_for())
        assert person.id not in _select(client).options
