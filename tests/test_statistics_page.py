"""Click paths for the Statistik page's time axis."""

from datetime import datetime

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain import period
from app.domain.demo_data import create_demo_data


def _page() -> Client:
    """Render the Statistik page against demo data."""
    from app.gui.pages import statistics as statistics_module

    with connection_scope() as connection:
        create_demo_data(connection)

    client = Client(ui.page("/probe-statistics")(lambda: None), request=None)
    with client:
        statistics_module.statistics_energy_page()
    return client


def _panel(client: Client, heading: str = "Energie"):
    """Find one thematic card by its heading."""
    for element in client.elements.values():
        if element.__class__.__name__ != "Card":
            continue
        labels = [
            child.text
            for child in element.descendants()
            if child.__class__.__name__ == "Label" and getattr(child, "text", None)
        ]
        if labels and labels[0] == heading:
            return element
    raise AssertionError(f"Karte {heading!r} nicht gefunden")


def _select(client: Client, label: str, heading: str = "Energie"):
    """Find one select by its label, inside one panel."""
    matches = [
        element
        for element in _panel(client, heading).descendants()
        if element.__class__.__name__ == "Select" and element._props.get("label") == label
    ]
    assert len(matches) == 1, f"Auswahlfeld {label!r} in {heading!r} nicht eindeutig: {len(matches)}"
    return matches[0]


def _button(client: Client, name: str, heading: str = "Energie"):
    """Find one button by its icon or label, inside one panel."""
    matches = [
        element
        for element in _panel(client, heading).descendants()
        if element.__class__.__name__ == "Button"
        and name in (element._props.get("icon"), element._props.get("label"))
    ]
    assert len(matches) == 1, f"Knopf {name!r} in {heading!r} nicht eindeutig: {len(matches)}"
    return matches[0]


def _press(button) -> None:
    """Invoke a button's click handler, as a real click would."""
    for listener in button._event_listeners.values():
        if listener.type == "click":
            listener.handler(None)
            return
    raise AssertionError("der Knopf hat keinen Click-Handler -- er tut nichts")


def _window_label(client: Client, heading: str = "Energie"):
    """The label naming the window currently shown in one panel."""
    matches = [
        element
        for element in _panel(client, heading).descendants()
        if element.__class__.__name__ == "Label" and "min-w-[180px]" in " ".join(element._classes)
    ]
    assert len(matches) == 1, f"genau eine Fensterbeschriftung in {heading!r} erwartet"
    return matches[0]


def _energy_chart(client: Client):
    """The chart inside the Energie panel."""
    charts = [e for e in _panel(client).descendants() if e.__class__.__name__ == "EChart"]
    assert len(charts) == 1, f"genau ein Energie-Diagramm erwartet, {len(charts)} gefunden"
    return charts[0]


def test_the_energy_chart_shows_the_locally_shared_share():
    """The figure the LEG exists for, and the percentage beside it."""
    client = _page()
    options = _energy_chart(client).options

    assert [series["name"] for series in options["series"]] == [
        "Bezug",
        "Einspeisung",
        "Lokal geteilt",
        "Eigenverbrauch",
    ]
    assert [axis["name"] for axis in options["yAxis"]] == ["kWh", "%"]


def test_switching_the_resolution_redraws_the_chart():
    """Each resolution brings its own window, point count and unit."""
    client = _page()
    chart = _energy_chart(client)
    resolution = _select(client, "Auflösung")

    seen = {}
    for key in (
        period.GRANULARITY_YEAR,
        period.GRANULARITY_MONTH,
        period.GRANULARITY_DAY,
        period.GRANULARITY_HOUR,
        period.GRANULARITY_QUARTER_HOUR,
    ):
        resolution.value = key
        seen[key] = len(chart.options["xAxis"]["data"])

    assert seen[period.GRANULARITY_YEAR] == 10
    assert seen[period.GRANULARITY_MONTH] == 12
    assert seen[period.GRANULARITY_DAY] in (90, 91, 92)
    assert seen[period.GRANULARITY_HOUR] == 168
    assert seen[period.GRANULARITY_QUARTER_HOUR] == 96


def test_the_quarter_hour_view_switches_the_unit_to_power():
    """A load curve is drawn in kW, everything coarser in kWh."""
    client = _page()
    chart = _energy_chart(client)
    resolution = _select(client, "Auflösung")

    resolution.value = period.GRANULARITY_QUARTER_HOUR
    assert chart.options["yAxis"][0]["name"] == "kW"

    resolution.value = period.GRANULARITY_HOUR
    assert chart.options["yAxis"][0]["name"] == "kWh"


def test_the_arrows_really_move_the_window():
    """Asserted on the window, not on the axis labels."""
    client = _page()
    _select(client, "Auflösung").value = period.GRANULARITY_QUARTER_HOUR
    label = _window_label(client)
    start = label.text

    _press(_button(client, "chevron_left"))
    one_back = label.text
    _press(_button(client, "chevron_left"))
    two_back = label.text
    _press(_button(client, "chevron_right"))

    assert one_back != start, "der Pfeil muss das Fenster bewegen"
    assert two_back != one_back
    assert label.text == one_back, "zurück und wieder vor landet am selben Ort"


def test_changing_the_resolution_keeps_the_window_in_view():
    """Switching from a day to its quarter keeps that day in sight."""
    client = _page()
    resolution = _select(client, "Auflösung")
    label = _window_label(client)

    resolution.value = period.GRANULARITY_QUARTER_HOUR
    for _ in range(120):
        _press(_button(client, "chevron_left"))
    day_shown = label.text

    resolution.value = period.GRANULARITY_DAY
    widened = label.text

    resolution.value = period.GRANULARITY_QUARTER_HOUR
    assert label.text == day_shown, "der Anker bleibt stehen, das Fenster wechselt nur die Breite"
    assert widened.startswith("Q"), widened


def test_heute_returns_to_the_current_window():
    """After paging away, one button gets back to now."""
    client = _page()
    _select(client, "Auflösung").value = period.GRANULARITY_QUARTER_HOUR
    label = _window_label(client)

    _press(_button(client, "Heute"))

    expected = period.window_label(
        period.GRANULARITY_QUARTER_HOUR,
        period.window_for(period.GRANULARITY_QUARTER_HOUR, datetime.now()),
    )
    assert label.text == expected


def test_the_chart_opens_where_the_data_is():
    """Not on today, which is routinely an empty window."""
    client = _page()
    chart = _energy_chart(client)
    _select(client, "Auflösung").value = period.GRANULARITY_MONTH

    assert any(value for value in chart.options["series"][0]["data"]), "beim Öffnen muss etwas zu sehen sein"


def test_an_empty_window_says_so_while_readings_exist_elsewhere():
    """A different statement from "nothing imported yet", and it has to be."""
    client = _page()
    _select(client, "Auflösung").value = period.GRANULARITY_QUARTER_HOUR
    _press(_button(client, "Heute"))

    texts = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", None)
    ]
    assert any("Keine Messdaten in diesem Zeitraum" in text for text in texts)
    assert not any("Noch keine Messdaten importiert" in text for text in texts)


def test_the_leg_filter_reaches_the_chart():
    """Selecting a LEG has to change the numbers, not just the label."""
    from app.gui.pages import statistics as statistics_module
    from app.models import leg as leg_repo
    from app.models.leg import Leg

    with connection_scope() as connection:
        create_demo_data(connection)
        empty_leg = leg_repo.create(
            connection,
            Leg(
                id=None,
                name="LEG ohne Messdaten",
                note="",
                created_at="",
                production_capacity_percent=None,
                production_capacity_recorded_at=None,
            ),
        )

    client = Client(ui.page("/probe-statistics-filter")(lambda: None), request=None)
    with client:
        statistics_module.statistics_energy_page()

    chart = _energy_chart(client)
    _select(client, "Auflösung").value = period.GRANULARITY_MONTH
    everything = sum(value or 0 for value in chart.options["series"][0]["data"])

    _select(client, "LEG").value = empty_leg
    just_the_empty_one = sum(value or 0 for value in chart.options["series"][0]["data"])

    assert everything > 0
    assert just_the_empty_one == 0, "eine LEG ohne Messpunkte kann nichts anzeigen"


def test_a_database_without_readings_says_why_the_chart_is_empty():
    """An unexplained white rectangle is what made this page feel dead."""
    from app.gui.pages import statistics as statistics_module

    client = Client(ui.page("/probe-statistics-empty")(lambda: None), request=None)
    with client:
        statistics_module.statistics_energy_page()

    texts = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", None)
    ]
    assert any("Noch keine Messdaten importiert" in text for text in texts)
