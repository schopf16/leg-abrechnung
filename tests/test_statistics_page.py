"""Click paths for the Statistik page's time axis.

Rendering the page proves only that it builds. The resolution select and
the arrows are the whole feature here, so they are driven for real -- the
lesson from `2a33e40`, where a page kept rendering perfectly while its
upload handler had been broken for nine days.

One trap this file exists to avoid: the arrows can look verified when they
are not. Every day carries the same axis labels ("00:00" ... "23:45"), so
a chart whose window never moved is indistinguishable from one that moved
correctly -- unless the *window* is what gets asserted.

Every lookup here is scoped to the **Energie** panel. The scoping stays
useful now that each theme has a page of its own: it is what keeps these
tests pointed at the chart they are about if a page ever grows a second
one, and it made the split from one page into four a change of the
rendered function and nothing else.
"""

from datetime import datetime

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain import period
from app.domain.demo_data import create_demo_data


def _page() -> Client:
    """Render the Statistik page against demo data.

    Returns:
        The client holding the rendered page.
    """
    from app.gui.pages import statistics as statistics_module

    with connection_scope() as connection:
        create_demo_data(connection)

    client = Client(ui.page("/probe-statistics")(lambda: None), request=None)
    with client:
        statistics_module.statistics_energy_page()
    return client


def _panel(client: Client, heading: str = "Energie"):
    """Find one thematic card by its heading.

    Args:
        client: The rendered client.
        heading: The panel's German heading.

    Returns:
        The card element, to search inside.
    """
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
    """Find one select by its label, inside one panel.

    Args:
        client: The rendered client.
        label: The German field label.
        heading: Which panel to look in.

    Returns:
        The matching select element.
    """
    matches = [
        element
        for element in _panel(client, heading).descendants()
        if element.__class__.__name__ == "Select" and element._props.get("label") == label
    ]
    assert len(matches) == 1, f"Auswahlfeld {label!r} in {heading!r} nicht eindeutig: {len(matches)}"
    return matches[0]


def _button(client: Client, name: str, heading: str = "Energie"):
    """Find one button by its icon or label, inside one panel.

    Args:
        client: The rendered client.
        name: The icon name or the button caption.
        heading: Which panel to look in.

    Returns:
        The matching button element.
    """
    matches = [
        element
        for element in _panel(client, heading).descendants()
        if element.__class__.__name__ == "Button"
        and name in (element._props.get("icon"), element._props.get("label"))
    ]
    assert len(matches) == 1, f"Knopf {name!r} in {heading!r} nicht eindeutig: {len(matches)}"
    return matches[0]


def _press(button) -> None:
    """Invoke a button's click handler, as a real click would.

    Args:
        button: The button element.

    Returns:
        None.
    """
    for listener in button._event_listeners.values():
        if listener.type == "click":
            listener.handler(None)
            return
    raise AssertionError("der Knopf hat keinen Click-Handler -- er tut nichts")


def _window_label(client: Client, heading: str = "Energie"):
    """The label naming the window currently shown in one panel.

    Args:
        client: The rendered client.
        heading: Which panel to look in.

    Returns:
        The label element.
    """
    matches = [
        element
        for element in _panel(client, heading).descendants()
        if element.__class__.__name__ == "Label" and "min-w-[180px]" in " ".join(element._classes)
    ]
    assert len(matches) == 1, f"genau eine Fensterbeschriftung in {heading!r} erwartet"
    return matches[0]


def _energy_chart(client: Client):
    """The chart inside the Energie panel.

    Found through its panel rather than by position: the page holds five
    charts now, and "the first one" would be a guess about their order.

    Args:
        client: The rendered client.

    Returns:
        The chart element.
    """
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
    """Asserted on the window, not on the axis labels.

    Every day's axis reads "00:00" to "23:45", so a chart that never moved
    looks exactly like one that moved correctly. Only the window label
    tells them apart -- which is why this test looks there.
    """
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
    """Switching from a day to its quarter keeps that day in sight.

    Jumping back to today on every resolution change would make the arrows
    useless -- you could never look at one day and then widen out around
    it.
    """
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
    """After paging away, one button gets back to now.

    Note the chart does **not** open on "now": it opens on the newest
    reading, because an import usually lands a completed quarter and an
    empty chart on arrival reads as a broken page. So "Heute" is a jump
    forward here, not a reset to where it started.
    """
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
    """Not on today, which is routinely an empty window.

    The demo readings lie in a past quarter; opening on "now" showed a
    blank chart until somebody thought to press the arrows, which is
    exactly the impression this page had to lose.
    """
    client = _page()
    chart = _energy_chart(client)
    _select(client, "Auflösung").value = period.GRANULARITY_MONTH

    assert any(value for value in chart.options["series"][0]["data"]), "beim Öffnen muss etwas zu sehen sein"


def test_an_empty_window_says_so_while_readings_exist_elsewhere():
    """A different statement from "nothing imported yet", and it has to be.

    One sends the reader to the import page, the other to the arrows.
    """
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
    """Selecting a LEG has to change the numbers, not just the label.

    The demo deployment has exactly one LEG holding readings, so the proof
    runs the other way round: a second, empty LEG must bring the chart to
    zero. Comparing "all LEGs" against the only LEG that has data would
    pass even if the filter were ignored entirely.

    The extra LEG is created **before** the page renders, because the
    select builds its options once -- an id that did not exist yet is not
    selectable, and the assignment would silently do nothing.
    """
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
