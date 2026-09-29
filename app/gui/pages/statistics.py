"""Statistik page: what the deployment looks like, and how the energy flows.

The energy chart is drawn on the shared time axis (`app.gui.time_axis`),
so the resolution is the reader's choice -- from a quarter-hour load curve
up to a decade -- and the window follows it.

Beside consumption and feed-in it shows the figure the LEG actually exists
for: **how much of the production found a taker inside the community**.
`app.domain.distribution` has always computed it, per 15-minute interval,
and then folded it straight into quarterly per-person totals; as a curve
it is the answer to "is this working", which the numbers alone never gave.

A chart with nothing in it says **why** it is empty rather than showing a
white rectangle. Three of this page's sources are empty until somebody
imports readings or runs a billing, and an unexplained blank is exactly
what made this page feel like "da ist nicht viel los".
"""

from datetime import datetime

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.period import (
    GRANULARITY_DAY,
    GRANULARITY_HOUR,
    GRANULARITY_MONTH,
    GRANULARITY_QUARTER_HOUR,
    GRANULARITY_YEAR,
    MONTH_NAMES_DE,
)
from app.domain.statistics import (
    energy_series,
    energy_unit,
    monthly_growth_counts,
)
from app.gui.navigation import page_frame
from app.gui.time_axis import render_time_axis
from app.models import leg as leg_repo

_MONTHS_SHOWN = 12

#: Resolutions the energy chart offers, finest first. All five: the
#: quarter-hour load curve is the one view that shows *why* a day shared
#: as little as it did, and the decade is where a LEG's growth shows.
_ENERGY_GRANULARITIES = [
    GRANULARITY_QUARTER_HOUR,
    GRANULARITY_HOUR,
    GRANULARITY_DAY,
    GRANULARITY_MONTH,
    GRANULARITY_YEAR,
]


def _empty_note(message: str) -> None:
    """Say in one line that a chart has nothing to draw.

    One grey line, no link, no explanation of what to do about it: this
    page is meant to be read as charts, and a paragraph above every empty
    one is what made it feel like more text than picture.

    Args:
        message: Why there is nothing to draw.

    Returns:
        None.
    """
    ui.label(message).classes("text-body2 text-grey-6")


def _month_label(year: int, month: int) -> str:
    """Format a calendar month as a short chart-axis label.

    Args:
        year: Calendar year.
        month: Calendar month, 1 to 12.

    Returns:
        A label such as "Sep 2025".
    """
    return f"{MONTH_NAMES_DE[month][:3]} {year}"


@ui.page("/statistics")
def statistics_page() -> None:
    """Render the Statistik page with energy-flow and growth charts.

    Returns:
        None.
    """
    with page_frame("/statistics", "Statistik"):
        with connection_scope() as connection:
            legs = leg_repo.list_all(connection)
            latest_reading = connection.execute("SELECT MAX(timestamp) FROM readings").fetchone()[0]
        has_readings = latest_reading is not None
        leg_options = {None: "Alle LEGs", **{leg.id: leg.name for leg in legs}}

        ui.label("Energiefluss").classes("text-lg font-bold mt-4")
        if not has_readings:
            _empty_note("Noch keine Messdaten importiert.")

        leg_select = ui.select(leg_options, value=None, label="LEG").classes("w-64")
        # Opened on the newest reading rather than on today: an import
        # usually lands a completed quarter, so "now" is routinely a window
        # with nothing in it -- and an empty chart on arrival reads as a
        # broken page, not as an empty period.
        energy_axis = render_time_axis(
            _ENERGY_GRANULARITIES,
            lambda: refresh_energy_chart(),
            anchor=datetime.fromisoformat(latest_reading) if latest_reading else None,
        )
        energy_empty_note = ui.label("").classes("text-body2 text-orange-9")
        energy_chart = ui.echart({}).classes("w-full").style("height: 380px")

        def refresh_energy_chart() -> None:
            """Reload the energy chart for the current LEG, window and resolution.

            Returns:
                None.
            """
            with connection_scope() as connection:
                series = energy_series(
                    connection, energy_axis.key, energy_axis.window, leg_id=leg_select.value
                )
            # Readings exist, but not here: a different statement from
            # "nothing imported yet", and the reader needs to be able to
            # tell them apart before reaching for the arrows.
            energy_empty_note.text = (
                "Keine Messdaten in diesem Zeitraum."
                if has_readings and not any(b.consumption_kwh or b.feed_in_kwh for b in series)
                else ""
            )
            unit = energy_unit(energy_axis.key)
            values = [bucket.value_for(energy_axis.key) for bucket in series]
            shares = [
                None
                if bucket.self_consumption_share is None
                else round(bucket.self_consumption_share * 100, 1)
                for bucket in series
            ]

            energy_chart.options.clear()
            energy_chart.options.update(
                {
                    "tooltip": {"trigger": "axis"},
                    "legend": {"data": ["Bezug", "Einspeisung", "Lokal geteilt", "Eigenverbrauch"]},
                    "xAxis": {"type": "category", "data": energy_axis.axis_labels()},
                    "yAxis": [
                        {"type": "value", "name": unit},
                        {"type": "value", "name": "%", "max": 100, "min": 0, "position": "right"},
                    ],
                    "series": [
                        {"name": "Bezug", "type": "bar", "data": [v[0] for v in values]},
                        {"name": "Einspeisung", "type": "bar", "data": [v[1] for v in values]},
                        {"name": "Lokal geteilt", "type": "line", "data": [v[2] for v in values]},
                        {
                            "name": "Eigenverbrauch",
                            "type": "line",
                            "yAxisIndex": 1,
                            # Gaps rather than zeros: at night nothing was
                            # fed in, which is not the same statement as
                            # "none of it was used" (see EnergyBucket).
                            "connectNulls": False,
                            "data": shares,
                        },
                    ],
                }
            )
            energy_chart.update()

        leg_select.on_value_change(lambda _: refresh_energy_chart())
        refresh_energy_chart()

        ui.label("Wachstum").classes("text-lg font-bold mt-6")
        with connection_scope() as connection:
            growth = monthly_growth_counts(connection, months=_MONTHS_SHOWN)
        ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                "legend": {"data": ["Personen", "Messpunkte", "Standorte", "Trafokreise", "LEGs"]},
                "xAxis": {"type": "category", "data": [_month_label(g.year, g.month) for g in growth]},
                "yAxis": {"type": "value"},
                "series": [
                    {"name": "Personen", "type": "line", "data": [g.persons for g in growth]},
                    {"name": "Messpunkte", "type": "line", "data": [g.metering_points for g in growth]},
                    {"name": "Standorte", "type": "line", "data": [g.sites for g in growth]},
                    {"name": "Trafokreise", "type": "line", "data": [g.substation_areas for g in growth]},
                    {"name": "LEGs", "type": "line", "data": [g.legs for g in growth]},
                ],
            }
        ).classes("w-full").style("height: 350px")
