"""The Stammdaten lists as paged tables.

"eine solche tabellenansicht ist näher an einer datenbank als diese
boubles" -- so Trafokreise, LEGs and Messpunkte took the same shape as
Personen and Standorte. A render test would pass on an empty table, so these
check that the rows arrive, that the cells read as one value each, and that
the buttons in the actions column are wired: a Vue template that emits an
event nobody listens for looks exactly like one that works.
"""

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
    """One Trafokreis, one Standort, one LEG and one Messpunkt.

    Returns:
        `{"area": id, "site": id, "leg": id, "metering_point": id}`.
    """
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
    """Render one list page and return its table.

    Args:
        module: Module name under `app.gui.pages`.
        function: The page function.
        probe: A unique probe route -- every `ui.page` registers itself.

    Returns:
        `(client, table)`.
    """
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
    """A Vue template emitting an event nobody listens for looks identical.

    The lesson from the nicegui 3.16 bump: rendering proves that a page
    builds, never that a click does anything.
    """
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
    """A Trafokreis has no detail page, so the triangle needs somewhere to lead.

    Without this the marker on that one list pointed at nothing: no eye to
    open, and the dialog said only what the record holds, not what is wrong
    with it. Driven through the pencil in the row, because the form is a
    nested function and only that path reaches it.
    """
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
