"""One search box that reaches every Stammdaten record."""

from datetime import date

import pytest
from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain.global_search import (
    KIND_LEG,
    KIND_METERING_POINT,
    KIND_ORDER,
    KIND_PERSON,
    KIND_SITE,
    KIND_SUBSTATION_AREA,
    MIN_QUERY_LENGTH,
    PER_KIND_LIMIT,
    search,
)
from app.models import assignment as assignment_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.assignment import Assignment
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, MeteringPoint
from app.models.person import Person
from app.models.site import Site
from app.models.substation_area import SubstationArea


def _deployment() -> dict:
    """One record of every kind, findable by street, name or designation."""
    with connection_scope() as connection:
        area = substation_area_repo.create(
            connection,
            SubstationArea(id=None, name="TRA9365", bkw_designation="TRA9365", note="", created_at=""),
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
                name="LEG Musterdorf",
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
                designation="CH1018000000000000000000042",
                label="Whg. 3. OG",
                direction=DIRECTION_CONSUMPTION,
                site_id=site,
                leg_id=leg,
                pv_capacity_kwp=None,
                battery_capacity_kwh=None,
                created_at="",
            ),
        )
        person = person_repo.create(
            connection,
            Person(
                id=None,
                salutation="",
                company="",
                first_name="Anna",
                last_name="Bühler",
                contact_email="",
                contact_phone="",
                billing_street="Zweitweg",
                billing_house_number="7",
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
        assignment_repo.create(
            connection,
            Assignment(
                id=None,
                metering_point_id=metering_point,
                person_id=person,
                valid_from=date(2026, 1, 1),
                valid_to=None,
                created_at="",
            ),
        )
    return {
        KIND_SUBSTATION_AREA: area,
        KIND_SITE: site,
        KIND_LEG: leg,
        KIND_METERING_POINT: metering_point,
        KIND_PERSON: person,
    }


def _groups(query: str) -> dict:
    """Run one search."""
    with connection_scope() as connection:
        return {group.kind: group for group in search(connection, query)}


def test_a_street_finds_the_site_and_the_meter_on_it():
    """The question that needed knowing where things are filed."""
    _deployment()

    groups = _groups("Erstweg")

    assert KIND_SITE in groups
    assert groups[KIND_SITE].hits[0].title.startswith("Erstweg 4")
    # The meter is at that address, so looking up the street finds it too.
    assert KIND_METERING_POINT in groups


def test_a_name_finds_the_person_and_their_meter():
    """Both directions: the lists already searched each way, and so does this."""
    _deployment()

    groups = _groups("Bühler")

    assert KIND_PERSON in groups
    assert groups[KIND_PERSON].hits[0].title == "Anna Bühler"
    assert KIND_METERING_POINT in groups, "wer auf dem Messpunkt sitzt, findet ihn"


def test_an_umlaut_folds_the_way_it_folds_everywhere_else():
    """ "Buhler" finds "Bühler", exactly as the sorting folds it."""
    _deployment()

    assert KIND_PERSON in _groups("Buhler")


def test_a_designation_is_found_by_its_tail():
    """Nobody types a 27-character identifier from the front."""
    _deployment()

    assert KIND_METERING_POINT in _groups("000042")


@pytest.mark.parametrize("query", ["", " ", "a"])
def test_a_query_too_short_to_mean_anything_finds_nothing(query):
    """One character matches most of the database and says nothing."""
    _deployment()

    assert _groups(query) == {}
    assert MIN_QUERY_LENGTH == 2


def test_the_groups_come_in_the_order_of_the_data_model():
    """The same order the drawer lists them in."""
    _deployment()

    with connection_scope() as connection:
        order = [group.kind for group in search(connection, "Musterdorf")]

    assert order == [kind for kind in KIND_ORDER if kind in order]
    assert len(order) > 1


def test_a_group_caps_itself_and_says_how_many_more():
    """A dropdown with ninety entries is a list, and a worse one."""
    with connection_scope() as connection:
        for index in range(PER_KIND_LIMIT + 4):
            leg_repo.create(
                connection,
                Leg(
                    id=None,
                    name=f"LEG Beispiel {index:02d}",
                    note="",
                    production_capacity_percent=None,
                    production_capacity_recorded_at=None,
                    created_at="",
                ),
            )

    group = _groups("Beispiel")[KIND_LEG]

    assert len(group.hits) == PER_KIND_LIMIT
    assert group.total == PER_KIND_LIMIT + 4


def test_a_hit_leads_to_the_record():
    """And a Trafokreis, which has no detail page, leads to its list."""
    ids = _deployment()

    assert _groups("Bühler")[KIND_PERSON].hits[0].route == f"/persons/{ids[KIND_PERSON]}"
    assert _groups("TRA9365")[KIND_SUBSTATION_AREA].hits[0].route == "/substation-areas"


# --- The box in the header -------------------------------------------------


def _box(probe: str):
    """Render the search box on its own."""
    from app.gui.global_search import render_global_search

    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        box = render_global_search()
    return client, box


def test_typing_fills_the_list_and_marks_the_first_hit():
    """So Enter after typing is one keystroke to the obvious answer -- and the mark says which answer..."""
    _deployment()
    client, box = _box("/probe-search-type")

    with client:
        box.field.value = "Bühler"
        box.update()

    assert box.groups
    assert box.marked == 0


def test_the_arrows_walk_every_group_as_one_list(press):
    """The groups are headings, not stops: the keys run straight through."""
    _deployment()
    client, box = _box("/probe-search-arrows")

    with client:
        box.field.value = "Musterdorf"
        box.update()
        total = sum(len(group.hits) for group in box.groups)
        assert total > 1, box.groups

        press("ArrowDown")
        assert box.marked == 1

        press("ArrowUp")
        assert box.marked == 0

        press("ArrowUp")
        assert box.marked == total - 1, "läuft um"


def test_escape_puts_the_list_away_and_keeps_the_text(press):
    """One press dismisses the list; the typed text stays."""
    _deployment()
    client, box = _box("/probe-search-escape")

    with client:
        box.field.value = "Bühler"
        box.update()
        press("Escape")

    assert box.groups == []
    assert box.field.value == "Bühler"


def test_enter_goes_to_the_marked_record(press, monkeypatch):
    """Driven, because a list that renders and does not navigate looks the same as one that works."""
    ids = _deployment()
    client, box = _box("/probe-search-enter")

    went: list[str] = []
    monkeypatch.setattr(ui.navigate, "to", lambda route, **_: went.append(route))

    with client:
        box.field.value = "Bühler"
        box.update()
        assert box.groups[0].kind == KIND_METERING_POINT
        press("Enter")

    assert went == [f"/metering-points/{ids[KIND_METERING_POINT]}"]


def test_the_person_is_one_arrow_away(press, monkeypatch):
    """The other half of the same ordering."""
    ids = _deployment()
    client, box = _box("/probe-search-enter-person")

    went: list[str] = []
    monkeypatch.setattr(ui.navigate, "to", lambda route, **_: went.append(route))

    with client:
        box.field.value = "Bühler"
        box.update()
        press("ArrowDown")
        press("Enter")

    assert went == [f"/persons/{ids[KIND_PERSON]}"]


def test_leaving_empties_the_box(monkeypatch):
    """Coming back to a page with yesterday's query in the header and no list under it reads as broken."""
    _deployment()
    client, box = _box("/probe-search-clears")
    monkeypatch.setattr(ui.navigate, "to", lambda *_, **__: None)

    with client:
        box.field.value = "Bühler"
        box.update()
        box.open("/persons/1")

    assert box.field.value == ""
    assert box.groups == []


def test_the_box_gives_the_keys_back_when_it_closes():
    """Otherwise it would answer Escape on the page underneath it."""
    from app.gui.keyboard import layers

    _deployment()
    client, box = _box("/probe-search-layer")

    with client:
        box.field.value = "Bühler"
        box.update()
        assert layers()[-1] is box._layer

        box.hide()
        assert box._layer not in layers()
