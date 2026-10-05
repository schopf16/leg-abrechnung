"""The Stammdaten lists as paged tables."""

import pytest
from nicegui import Client, ui

from app.db.connection import connection_scope
from app.gui.table_list import DEFAULT_PAGE_SIZE
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_FEED_IN, MeteringPoint
from app.models.site import Site
from app.models.substation_area import SubstationArea


def _deployment() -> dict:
    """One Trafokreis, one Standort, one LEG and one Messpunkt."""
    with connection_scope() as connection:
        area = substation_area_repo.create(
            connection,
            SubstationArea(
                id=None,
                name="TRA9365",
                bkw_designation="TRA9365",
                note="Beispiel-Bemerkung",
                created_at="",
            ),
        )
        site = site_repo.create(
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
        leg = leg_repo.create(
            connection,
            Leg(
                id=None,
                name="LEG Beispiel",
                note="",
                production_capacity_percent=None,
                production_capacity_recorded_at=None,
                created_at="",
            ),
        )
        metering_point = metering_point_repo.create(
            connection,
            MeteringPoint(
                id=None,
                designation="CH1018000000000000000000001",
                label="Whg. 3. OG",
                direction=DIRECTION_FEED_IN,
                site_id=site,
                leg_id=leg,
                pv_capacity_kwp=None,
                battery_capacity_kwh=None,
                created_at="",
            ),
        )
    return {"area": area, "site": site, "leg": leg, "metering_point": metering_point}


def _table(module: str, function: str, probe: str):
    """Render one list page and return its table."""
    import importlib

    page_module = importlib.import_module(f"app.gui.pages.{module}")
    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        getattr(page_module, function)()
    table = next(element for element in client.elements.values() if element.__class__.__name__ == "Table")
    return client, table


@pytest.mark.parametrize(
    "module, function, probe, labels",
    [
        (
            "substation_areas",
            "substation_areas_page",
            "/probe-st-areas",
            ["Name", "BKW-Bezeichnung", "Standorte", "Produzenten / Konsumenten", "Bemerkung", ""],
        ),
        (
            "legs",
            "legs_page",
            "/probe-st-legs",
            [
                "Name",
                "Messpunkte",
                "Produzenten / Konsumenten",
                "Produktionsleistung",
                "Trafokreise",
                "",
            ],
        ),
        (
            "metering_points",
            "metering_points_page",
            "/probe-st-mp",
            ["Messpunkt", "Bezeichnung", "Messrichtung", "Standort", "LEG", "Zugeordnet", ""],
        ),
    ],
)
def test_each_list_is_a_paged_table_with_its_own_columns(module, function, probe, labels):
    """The columns are the list's contract with the reader."""
    _deployment()

    _, table = _table(module, function, probe)

    assert [column["label"] for column in table.columns] == labels
    assert table._props["pagination"]["rowsPerPage"] == DEFAULT_PAGE_SIZE
    assert "leg-selectable" in table._classes, "eine Zelle muss kopierbar sein"


def test_the_trafokreis_row_reads_as_one_value_per_cell():
    """The card drew name and BKW designation stacked; a row has columns."""
    _deployment()

    _, table = _table("substation_areas", "substation_areas_page", "/probe-st-areas-row")

    row = table.rows[0]
    assert row["name"] == "TRA9365"
    assert row["bkw_designation"] == "TRA9365"
    assert row["sites_count"] == 1
    assert row["note"] == "Beispiel-Bemerkung"


def test_the_messpunkt_row_holds_the_site_in_one_cell():
    """Two lines on the card, one cell here."""
    _deployment()

    _, table = _table("metering_points", "metering_points_page", "/probe-st-mp-row")

    row = table.rows[0]
    assert row["site"] == "Erstweg 4, 3048 Musterdorf"
    assert row["label"] == "Whg. 3. OG"
    assert row["direction"] == "Einspeisung"
    assert row["leg"] == "LEG Beispiel"


def test_the_leg_row_holds_the_trafokreise_in_one_cell():
    """The card drew a status line with the names indented underneath."""
    _deployment()

    _, table = _table("legs", "legs_page", "/probe-st-legs-row")

    assert "TRA9365" in table.rows[0]["substation_areas"]


@pytest.mark.parametrize(
    "module, function, probe",
    [
        ("substation_areas", "substation_areas_page", "/probe-st-areas-edit"),
        ("legs", "legs_page", "/probe-st-legs-edit"),
        ("metering_points", "metering_points_page", "/probe-st-mp-edit"),
    ],
)
def test_the_pencil_in_the_actions_column_opens_the_dialog(module, function, probe):
    """A Vue template emitting an event nobody listens for looks identical."""
    _deployment()

    client, table = _table(module, function, probe)
    row = table.rows[0]

    before = sum(1 for element in client.elements.values() if element.__class__.__name__ == "Dialog")
    with client:
        handler = next(
            listener.handler for listener in table._event_listeners.values() if listener.type == "edit"
        )
        handler(type("Event", (), {"args": row})())

    after = sum(1 for element in client.elements.values() if element.__class__.__name__ == "Dialog")
    assert after > before, "der Stift muss einen Dialog öffnen"


def test_a_trafokreis_finding_can_be_read_in_the_pencil(address_register):
    """A Trafokreis has no detail page, so the triangle needs somewhere to lead."""
    _deployment()

    client, table = _table("substation_areas", "substation_areas_page", "/probe-st-areas-finding")
    row = table.rows[0]
    assert row["has_problem"] is True, "ein Trafokreis mit nur Einspeisung ist ein Befund"

    with client:
        handler = next(
            listener.handler for listener in table._event_listeners.values() if listener.type == "edit"
        )
        handler(type("Event", (), {"args": row})())

    texts = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", "")
    ]
    assert "Zu prüfen" in texts, texts
    # One feed-in meter and no consumption: nothing can be shared there.
    assert any("Produzenten" in text for text in texts), texts


# --- Zuordnungen, and the width every table has to live within -------------


def _two_assignments() -> dict:
    """One Messpunkt with two people in sequence, which is a move."""
    from datetime import date

    from app.models import assignment as assignment_repo
    from app.models import person as person_repo
    from app.models.assignment import Assignment
    from app.models.person import Person

    ids = _deployment()
    with connection_scope() as connection:
        people = []
        for last_name in ("Vorher", "Nachher"):
            people.append(
                person_repo.create(
                    connection,
                    Person(
                        id=None,
                        salutation="",
                        company="",
                        first_name="Anna",
                        last_name=last_name,
                        contact_email="",
                        contact_phone="",
                        billing_street="Erstweg",
                        billing_house_number="4",
                        billing_postal_code="3048",
                        billing_city="Musterdorf",
                        billing_country="CH",
                        iban="",
                        paper_invoice=False,
                        note="",
                        customer_number=None,
                        bkw_customer_number=None,
                        active=True,
                        created_at="",
                    ),
                )
            )
        assignment_repo.create(
            connection,
            Assignment(
                id=None,
                metering_point_id=ids["metering_point"],
                person_id=people[0],
                valid_from=date(2026, 1, 1),
                valid_to=date(2026, 6, 30),
                created_at="",
            ),
        )
        assignment_repo.create(
            connection,
            Assignment(
                id=None,
                metering_point_id=ids["metering_point"],
                person_id=people[1],
                valid_from=date(2026, 7, 1),
                valid_to=None,
                created_at="",
            ),
        )
    return ids


def test_zuordnungen_is_a_table_with_one_row_per_assignment():
    """It was a card per Messpunkt with its assignments listed inside."""
    _two_assignments()

    _, table = _table("assignments", "assignments_page", "/probe-st-assign")

    assert [column["label"] for column in table.columns] == [
        "Messpunkt",
        "Standort",
        "Person",
        "Gültig von",
        "Gültig bis",
        "",
    ]
    assert len(table.rows) == 2


def test_the_rows_of_one_messpunkt_stay_together_and_in_sequence():
    """That is the part of the grouping worth keeping."""
    _two_assignments()

    _, table = _table("assignments", "assignments_page", "/probe-st-assign-order")

    names = [row["person_name"] for row in table.rows]
    assert names == ["Anna Vorher", "Anna Nachher"]
    assert len({row["metering_point"] for row in table.rows}) == 1


def test_the_dates_are_written_the_way_the_rest_of_the_app_writes_them():
    """The card printed `2026-01-01`; every other page prints 01.01.2026."""
    _two_assignments()

    _, table = _table("assignments", "assignments_page", "/probe-st-assign-dates")

    assert table.rows[0]["valid_from"] == "01.01.2026"
    assert table.rows[0]["valid_to"] == "30.06.2026"
    assert table.rows[1]["valid_to"] == "offen"


def test_the_pencil_on_a_zuordnung_opens_its_dialog():
    """Driven, because an unwired slot looks the same as a wired one."""
    _two_assignments()

    client, table = _table("assignments", "assignments_page", "/probe-st-assign-edit")
    before = sum(1 for element in client.elements.values() if element.__class__.__name__ == "Dialog")

    with client:
        handler = next(
            listener.handler for listener in table._event_listeners.values() if listener.type == "edit"
        )
        handler(type("Event", (), {"args": table.rows[0]})())

    after = sum(1 for element in client.elements.values() if element.__class__.__name__ == "Dialog")
    assert after > before


@pytest.mark.parametrize(
    "module, function, probe",
    [
        ("substation_areas", "substation_areas_page", "/probe-st-wrap-areas"),
        ("legs", "legs_page", "/probe-st-wrap-legs"),
        ("metering_points", "metering_points_page", "/probe-st-wrap-mp"),
        ("assignments", "assignments_page", "/probe-st-wrap-assign"),
        ("sites", "sites_page", "/probe-st-wrap-sites"),
        ("persons", "persons_page", "/probe-st-wrap-persons"),
    ],
)
def test_no_table_pushes_itself_wider_than_the_window(module, function, probe, address_register):
    """A sideways scrollbar sits at the *bottom* of a long list."""
    _deployment()

    _, table = _table(module, function, probe)

    assert table._props.get("wrap-cells") is True


def test_the_content_area_is_wide_enough_for_a_seven_column_table():
    """1024 px was set when every list was cards that wrapped freely."""
    from app.gui.navigation import page_frame

    client = Client(ui.page("/probe-st-width")(lambda: None), request=None)
    with client:
        with page_frame("/metering-points", "Messpunkte") as content:
            ui.label("Inhalt")

    assert "max-w-screen-2xl" in content._classes
    assert "max-w-5xl" not in content._classes


# --- The pencil on a detail page -------------------------------------------


@pytest.mark.parametrize(
    "module, function, route, label",
    [
        ("sites", "site_detail_page", "/sites", "Standorte"),
        ("metering_points", "metering_point_detail_page", "/metering-points", "Messpunkte"),
        ("legs", "leg_detail_page", "/legs", "LEGs"),
        ("persons", "persons_page", "/persons", "Personen"),
    ],
)
def test_a_detail_page_says_where_it_is_and_can_be_edited(module, function, route, label, address_register):
    """Three of the four had no edit button at all."""
    import importlib

    ids = _deployment()
    if function == "persons_page":
        # Persons already had its button; the breadcrumb is what is new, and
        # its detail page needs a person to exist.
        from app.models import person as person_repo
        from app.models.person import Person

        with connection_scope() as connection:
            record_id = person_repo.create(
                connection,
                Person(
                    id=None,
                    salutation="",
                    company="",
                    first_name="Anna",
                    last_name="Muster",
                    contact_email="",
                    contact_phone="",
                    billing_street="Erstweg",
                    billing_house_number="4",
                    billing_postal_code="3048",
                    billing_city="Musterdorf",
                    billing_country="CH",
                    iban="",
                    paper_invoice=False,
                    note="",
                    customer_number=None,
                    bkw_customer_number=None,
                    active=True,
                    created_at="",
                ),
            )
        function = "person_detail_page"
    else:
        record_id = {
            "/sites": ids["site"],
            "/metering-points": ids["metering_point"],
            "/legs": ids["leg"],
        }[route]

    page_module = importlib.import_module(f"app.gui.pages.{module}")
    client = Client(ui.page(f"/probe-detail-header-{route}")(lambda: None), request=None)
    with client:
        getattr(page_module, function)(record_id)

    links = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Link" and element.text == label
    ]
    assert links, f"keine Brotkrume auf {label}"

    buttons = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Button" and element.text == "Bearbeiten"
    ]
    assert len(buttons) == 1, f"{label}: {len(buttons)} Bearbeiten-Knöpfe"

    # Driven, because a button that opens nothing looks the same.
    before = sum(1 for element in client.elements.values() if element.__class__.__name__ == "Dialog")
    with client:
        handler = next(
            listener.handler for listener in buttons[0]._event_listeners.values() if listener.type == "click"
        )
        handler(None)
    after = sum(1 for element in client.elements.values() if element.__class__.__name__ == "Dialog")
    assert after > before, f"{label}: der Stift öffnet keinen Dialog"


def test_saving_from_a_detail_page_reloads_it(address_register, monkeypatch):
    """The whole loop, end to end, because its last step is easy to miss."""
    from app.gui.pages.sites import site_detail_page
    from app.models import site as site_repo

    ids = _deployment()
    reloaded: list[bool] = []
    monkeypatch.setattr(ui.navigate, "reload", lambda: reloaded.append(True))

    client = Client(ui.page("/probe-detail-save")(lambda: None), request=None)
    with client:
        site_detail_page(ids["site"])

        pencil = next(
            element
            for element in client.elements.values()
            if element.__class__.__name__ == "Button" and element.text == "Bearbeiten"
        )
        next(listener.handler for listener in pencil._event_listeners.values() if listener.type == "click")(
            None
        )

        detail = next(
            element
            for element in client.elements.values()
            if element.__class__.__name__ == "Input" and element.label == "Lage (optional, z. B. Stockwerk)"
        )
        detail.value = "2. OG"

        save = [
            element
            for element in client.elements.values()
            if element.__class__.__name__ == "Button" and element.text == "Speichern"
        ][-1]
        next(listener.handler for listener in save._event_listeners.values() if listener.type == "click")(
            None
        )

    with connection_scope() as connection:
        assert site_repo.get(connection, ids["site"]).address_detail == "2. OG"
    assert reloaded == [True], "die Detailseite muss sich neu aufbauen"
