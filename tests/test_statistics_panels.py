"""Tests for the Statistik views: Energie, Wachstum, Debitorenverlauf,
Verteilung.

Four pages, each answering one question. Three of the four have nothing to
draw in a fresh deployment, so what they say when empty is tested as
carefully as what they draw when full: an unexplained blank is what made
this part of the app feel dead in the first place.
"""

from datetime import date

import pytest
from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain.statistics import distribution_by_leg, onboarding_funnel
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import person_onboarding as onboarding_repo
from app.models import site as site_repo
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.person import Person
from app.models.person_onboarding import STEPS
from app.models.site import Site


def _person(db, last_name: str) -> int:
    """Create a person.

    Args:
        db: Database connection fixture.
        last_name: Their surname.

    Returns:
        The new person's id.
    """
    return person_repo.create(
        db,
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


def _onboarding_at(db, person_id: int, completed_steps: int) -> None:
    """Start an onboarding and date its first `completed_steps` steps.

    Args:
        db: Database connection fixture.
        person_id: Whose onboarding.
        completed_steps: How many steps are already done, so the tracker
            waits on the next one.

    Returns:
        None.
    """
    tracker = onboarding_repo.start_for_person(db, person_id)
    for attribute, _ in STEPS[:completed_steps]:
        setattr(tracker, attribute, date(2026, 1, 1))
    onboarding_repo.update(db, tracker)


def _leg(db, name: str) -> int:
    """Create a LEG.

    Args:
        db: Database connection fixture.
        name: Its name.

    Returns:
        The new LEG's id.
    """
    return leg_repo.create(
        db,
        Leg(
            id=None,
            name=name,
            note="",
            created_at="",
            production_capacity_percent=None,
            production_capacity_recorded_at=None,
        ),
    )


def _meter(db, leg_id: int, direction: str, suffix: str, pv: float | None = None) -> None:
    """Create one metering point in a LEG.

    Args:
        db: Database connection fixture.
        leg_id: Its LEG.
        direction: Consumption or feed-in.
        suffix: Two digits making the designation unique.
        pv: Installed PV power, or `None`.

    Returns:
        None.
    """
    site_id = site_repo.create(
        db,
        Site(
            id=None,
            street="Weg",
            house_number=suffix,
            postal_code="3063",
            municipality="Ittigen",
            address_detail="",
            substation_area_id=None,
            created_at="",
        ),
    )
    metering_point_repo.create(
        db,
        MeteringPoint(
            id=None,
            designation=f"CH10180000000000000000000{suffix}",
            direction=direction,
            site_id=site_id,
            leg_id=leg_id,
            pv_capacity_kwp=pv,
            battery_capacity_kwh=None,
            created_at="",
        ),
    )


# --- The onboarding funnel ----------------------------------------------


def test_the_funnel_counts_people_at_the_step_they_wait_on(db):
    """Not a cumulative funnel: the useful question is what holds people up.

    Somebody with three steps dated is waiting on the fourth, and that is
    where they are counted.
    """
    _onboarding_at(db, _person(db, "Neu"), 0)
    _onboarding_at(db, _person(db, "Eingeteilt"), 2)
    _onboarding_at(db, _person(db, "Vertrag"), 3)
    _onboarding_at(db, _person(db, "Auch"), 3)
    _onboarding_at(db, _person(db, "Fertig"), len(STEPS))

    steps, completed = onboarding_funnel(db)
    waiting = {step.label: step.waiting for step in steps}

    assert waiting[STEPS[0][1]] == 1, "ohne datierten Schritt wartet man auf den ersten"
    assert waiting[STEPS[2][1]] == 1
    assert waiting[STEPS[3][1]] == 2
    assert completed == 1


def test_every_step_appears_even_with_nobody_on_it(db):
    """A step that happens to be empty must not vanish from the chart."""
    _onboarding_at(db, _person(db, "Eine"), 4)

    steps, _ = onboarding_funnel(db)

    assert [step.label for step in steps] == [label for _, label in STEPS]
    assert sum(step.waiting for step in steps) == 1


def test_the_funnel_and_the_completed_count_cover_every_tracker(db):
    """Nobody may fall between the two numbers."""
    for index in range(4):
        _onboarding_at(db, _person(db, f"P{index}"), index)
    _onboarding_at(db, _person(db, "Fertig"), len(STEPS))

    steps, completed = onboarding_funnel(db)

    assert sum(step.waiting for step in steps) + completed == len(onboarding_repo.list_all(db))


# --- Distribution across the LEGs ---------------------------------------


def test_the_distribution_splits_metering_points_by_direction(db):
    """And carries the installed power beside them."""
    leg_id = _leg(db, "LEG Eins")
    _meter(db, leg_id, DIRECTION_FEED_IN, "01", pv=10.0)
    _meter(db, leg_id, DIRECTION_CONSUMPTION, "02")
    _meter(db, leg_id, DIRECTION_CONSUMPTION, "03")

    distribution = distribution_by_leg(db)[0]

    assert distribution.feed_in_metering_points == 1
    assert distribution.consumption_metering_points == 2
    assert distribution.metering_points == 3
    assert distribution.pv_kwp == 10.0


def test_the_largest_leg_comes_first(db):
    """So an imbalance is visible without reading every bar."""
    small = _leg(db, "Klein")
    large = _leg(db, "Gross")
    _meter(db, small, DIRECTION_CONSUMPTION, "01")
    for suffix in ("02", "03", "04"):
        _meter(db, large, DIRECTION_CONSUMPTION, suffix)

    assert [d.name for d in distribution_by_leg(db)] == ["Gross", "Klein"]


def test_a_leg_without_metering_points_is_still_listed(db):
    """A LEG that was created and never filled is worth seeing as empty."""
    _leg(db, "Leer")

    distribution = distribution_by_leg(db)

    assert len(distribution) == 1
    assert distribution[0].metering_points == 0
    assert distribution[0].pv_kwp == 0.0


def test_every_metering_point_lands_in_exactly_one_leg(db):
    """The sum across the LEGs must be the deployment's own count."""
    first = _leg(db, "Eins")
    second = _leg(db, "Zwei")
    _meter(db, first, DIRECTION_FEED_IN, "01")
    _meter(db, second, DIRECTION_CONSUMPTION, "02")
    _meter(db, second, DIRECTION_CONSUMPTION, "03")

    total = sum(d.metering_points for d in distribution_by_leg(db))

    assert total == len(metering_point_repo.list_all(db))


# --- The panels on screen -----------------------------------------------


#: The four views, by the name of the page function that renders each.
_VIEWS = {
    "energy": "statistics_energy_page",
    "growth": "statistics_growth_page",
    "receivables": "statistics_receivables_page",
    "distribution": "statistics_distribution_page",
}


def _render(view: str) -> Client:
    """Render one Statistik view.

    Args:
        view: One of `_VIEWS`' keys.

    Returns:
        The client holding the rendered page.
    """
    from app.gui.pages import statistics as statistics_module

    client = Client(ui.page(f"/probe-{view}")(lambda: None), request=None)
    with client:
        getattr(statistics_module, _VIEWS[view])()
    return client


def _texts(client: Client) -> list[str]:
    """Every label text on the page.

    Args:
        client: The rendered client.

    Returns:
        The non-empty texts.
    """
    return [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", None)
    ]


@pytest.mark.parametrize(
    "view, heading",
    [
        ("energy", "Energie"),
        ("growth", "Personen"),
        ("receivables", "Debitoren und Rechnungen"),
        ("distribution", "Verteilung auf die LEGs"),
    ],
)
def test_each_view_shows_its_own_theme_and_nothing_else(view, heading):
    """One theme per page -- that is the point of having four.

    Four charts on one page meant the one you wanted was never the one in
    front of you.
    """
    texts = _texts(_render(view))

    assert heading in texts
    others = {"Energie", "Personen", "Debitoren und Rechnungen", "Verteilung auf die LEGs"} - {heading}
    assert not (others & set(texts)), f"{view} zeigt fremde Themen: {others & set(texts)}"


@pytest.mark.parametrize(
    "view, message",
    [
        ("energy", "Noch keine Messdaten importiert."),
        ("receivables", "Noch kein Abrechnungslauf erstellt."),
        ("growth", "Noch keine Aufnahmen erfasst."),
    ],
)
def test_each_empty_view_names_its_own_reason(view, message):
    """Three of the four are empty in a fresh deployment.

    Each says which source is missing, because "import readings" and "run a
    billing" are different actions and an unexplained blank suggests
    neither.
    """
    assert message in _texts(_render(view))


def test_the_funnel_reads_top_down_in_process_order():
    """ECharts puts index 0 at the bottom of a category axis.

    Regression test for exactly that: the chart first came out upside
    down, with the finished ones on top and the first step at the bottom.
    """
    with connection_scope() as connection:
        _onboarding_at(connection, _person(connection, "Wartend"), 3)

    charts = [e for e in _render("growth").elements.values() if e.__class__.__name__ == "EChart"]
    funnel = next(
        chart
        for chart in charts
        if isinstance(chart.options.get("yAxis"), dict) and chart.options["yAxis"].get("type") == "category"
    )

    axis = funnel.options["yAxis"]["data"]
    assert axis[0] == "abgeschlossen", "unten stehen die Fertigen"
    assert axis[-1] == STEPS[0][1], "oben der erste Schritt"


def _resolution_select(client: Client):
    """The one resolution select on a view.

    Args:
        client: The rendered client.

    Returns:
        The select element.
    """
    matches = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Select" and element._props.get("label") == "Auflösung"
    ]
    assert len(matches) == 1, f"genau eine Zeitachse je Seite erwartet, {len(matches)} gefunden"
    return matches[0]


def _offered_resolutions(client: Client) -> set[str]:
    """Which resolutions a view's axis offers.

    Args:
        client: The rendered client.

    Returns:
        The option keys.
    """
    options = _resolution_select(client).options
    return set(options if isinstance(options, list) else options.keys())


def test_the_receivables_view_offers_no_quarter_hour():
    """Invoices do not happen at that resolution.

    Offering it would produce a flat line with 96 points and invite the
    reader to look for something that cannot be there.
    """
    offered = _offered_resolutions(_render("receivables"))

    assert "quarter_hour" not in offered
    assert "hour" not in offered
    assert {"day", "month", "year"} <= offered


def test_the_energy_view_offers_every_resolution():
    """Including the quarter-hour load curve, which is the point of it."""
    assert {"quarter_hour", "hour", "day", "month", "year"} <= _offered_resolutions(_render("energy"))


@pytest.mark.parametrize("view", ["growth", "distribution"])
def test_a_view_without_a_time_series_has_no_time_axis(view):
    """Controls that would do nothing do not belong on the screen.

    The funnel and the per-LEG distribution are snapshots of now; arrows
    to page through windows would be four widgets promising something
    they cannot deliver.
    """
    client = _render(view)
    arrows = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Button" and element._props.get("icon") == "chevron_left"
    ]

    assert arrows == [], view


# --- The Verteilung view ------------------------------------------------
#
# Rebuilt after the administrator found the first attempt unconvincing: it
# carried stacked metering-point bars *and* a PV-capacity line on a second
# axis, over seven categories -- three quantities, two units, no clear
# question. Now two pies answer "which LEG is how big" and a table carries
# what a pie cannot say.


def test_the_view_shows_one_pie_per_unit():
    """Metering points and kWp are different units.

    One pie cannot hold both, and putting them on two axes of one bar
    chart is what made the first version unreadable.
    """
    with connection_scope() as connection:
        leg_id = _leg(connection, "LEG Eins")
        _meter(connection, leg_id, DIRECTION_FEED_IN, "01", pv=10.0)
        _meter(connection, leg_id, DIRECTION_CONSUMPTION, "02")

    charts = [e for e in _render("distribution").elements.values() if e.__class__.__name__ == "EChart"]

    assert len(charts) == 2
    assert all(chart.options["series"][0]["type"] == "pie" for chart in charts)
    values = [[slice_["value"] for slice_ in chart.options["series"][0]["data"]] for chart in charts]
    assert [2] in values, "zwei Messpunkte"
    assert [10.0] in values, "zehn kWp"


def test_the_table_carries_what_a_pie_cannot_say():
    """Two LEGs of equal size, and the direction split.

    No pie shows that two slices are exactly equal, and none can express
    a split within a slice at all -- which is the whole reason the table
    stayed.
    """
    with connection_scope() as connection:
        for index, name in enumerate(("LEG A", "LEG B")):
            leg_id = _leg(connection, name)
            _meter(connection, leg_id, DIRECTION_FEED_IN, f"{index}1", pv=5.0)
            _meter(connection, leg_id, DIRECTION_CONSUMPTION, f"{index}2")

    tables = [e for e in _render("distribution").elements.values() if e.__class__.__name__ == "Table"]

    assert len(tables) == 1
    assert [column["label"] for column in tables[0].columns] == [
        "LEG",
        "Bezug",
        "Einspeisung",
        "Messpunkte",
        "kWp",
    ]
    rows = {row["name"]: row for row in tables[0].rows}
    assert rows["LEG A"]["total"] == rows["LEG B"]["total"] == 2
    assert rows["LEG A"]["feed_in"] == 1
    assert rows["LEG A"]["consumption"] == 1
    assert rows["LEG A"]["pv"] == "5,00", "deutsche Schreibweise wie auf den Kacheln"


def test_a_leg_without_pv_is_in_the_table_but_not_in_the_pv_pie():
    """A zero slice would be invisible anyway, and the table has the fact.

    Drawing it would put a legend entry on a pie for something with no
    area, which reads as a rendering fault rather than as a zero.
    """
    with connection_scope() as connection:
        with_pv = _leg(connection, "Mit PV")
        without = _leg(connection, "Ohne PV")
        _meter(connection, with_pv, DIRECTION_FEED_IN, "01", pv=8.0)
        _meter(connection, without, DIRECTION_CONSUMPTION, "02")

    client = _render("distribution")
    charts = [e for e in client.elements.values() if e.__class__.__name__ == "EChart"]
    pv_pie = next(
        chart
        for chart in charts
        if any(slice_["value"] == 8.0 for slice_ in chart.options["series"][0]["data"])
    )
    table = next(e for e in client.elements.values() if e.__class__.__name__ == "Table")

    assert [slice_["name"] for slice_ in pv_pie.options["series"][0]["data"]] == ["Mit PV"]
    assert {row["name"] for row in table.rows} == {"Mit PV", "Ohne PV"}


def test_the_pies_carry_no_slice_labels():
    """LEG names are too long for a slice, and a truncated one is worse.

    ECharts shortened them to "LEG-Itti…", which took the space and said
    nothing. The hover gives the full name, value and share, and every
    name is spelled out in the table below -- so the slices stay bare.
    """
    with connection_scope() as connection:
        leg_id = _leg(connection, "LEG mit einem sehr langen Namen")
        _meter(connection, leg_id, DIRECTION_FEED_IN, "01", pv=4.0)

    charts = [e for e in _render("distribution").elements.values() if e.__class__.__name__ == "EChart"]

    for chart in charts:
        series = chart.options["series"][0]
        assert series["label"] == {"show": False}
        assert series["labelLine"] == {"show": False}
        assert chart.options["tooltip"]["formatter"] == "{b}: {c} ({d}%)", (
            "der Name muss beim Überfahren erscheinen, wenn er nicht am Stück steht"
        )
