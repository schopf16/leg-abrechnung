"""Statistik: five pages, one per theme -- Energie, Wachstum,
Debitorenverlauf, Verteilung, Ausgewogenheit.

Four routes rather than four cards on one page. They started as cards, and
that was still one page to scroll: a chart is worth a screen, and four of
them stacked means the one you want is never the one in front of you. The
side navigation's Statistik group lists them, so picking a theme is a click
rather than a scroll. No `ui.tabs` -- the project uses none, and the
navigation already does this job everywhere else.

The energy panel is drawn on the shared time axis
(`app.gui.time_axis`), so the resolution is the reader's choice, from a
quarter-hour load curve up to a decade, and the window follows it. Beside
consumption and feed-in it shows the figure the LEG actually exists for:
**how much of the production found a taker inside the community**.
`app.domain.distribution` has always computed it, per 15-minute interval,
and then folded it straight into quarterly per-person totals; as a curve it
answers "is this working", which the numbers alone never did.

A chart with nothing in it says **why** in one grey line rather than
showing a white rectangle, and nothing more: an unexplained blank is what
made this page feel empty, and a paragraph above every chart is what made
it feel like more text than picture.

The Debitoren panel reports the **LEG's own account only** -- invoiced,
received, and the open amount that should be coming down. Never a single
person's balance: the administrator ruled that out on data-protection
grounds, and the reasoning holds. A statistics page is read over somebody's
shoulder; a person's detail page is not.
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
    shift_window_one_year,
)
from app.domain.production_capacity import format_factor, format_percent
from app.domain.statistics import (
    distribution_by_leg,
    leg_balance,
    energy_series,
    energy_unit,
    monthly_growth_counts,
    onboarding_funnel,
    receivables_series,
)
from app.gui.navigation import page_frame
from app.gui.time_axis import render_time_axis
from app.models import leg as leg_repo

_MONTHS_SHOWN = 12

#: Resolutions the energy chart offers, finest first. All five: the
#: quarter-hour load curve is the one view that shows *why* a day shared as
#: little as it did, and the decade is where a LEG's growth shows.
_ENERGY_GRANULARITIES = [
    GRANULARITY_QUARTER_HOUR,
    GRANULARITY_HOUR,
    GRANULARITY_DAY,
    GRANULARITY_MONTH,
    GRANULARITY_YEAR,
]

#: Resolutions the receivables chart offers. No quarter-hour or hour:
#: invoices and payments do not happen at that resolution, and offering it
#: would only produce a flat line with 96 points.
_MONEY_GRANULARITIES = [GRANULARITY_DAY, GRANULARITY_MONTH, GRANULARITY_YEAR]

#: Height every chart on this page is drawn at.
_CHART_HEIGHT = "height: 360px"


def _month_label(year: int, month: int) -> str:
    """Format a calendar month as a short chart-axis label.

    Args:
        year: Calendar year.
        month: Calendar month, 1 to 12.

    Returns:
        A label such as "Sep 2025".
    """
    return f"{MONTH_NAMES_DE[month][:3]} {year}"


def _empty_note(message: str) -> None:
    """Say in one line that a chart has nothing to draw.

    One grey line, no link, no advice: this page is meant to be read as
    charts, and a paragraph above every empty one is what made it feel like
    more text than picture.

    Args:
        message: Why there is nothing to draw.

    Returns:
        None.
    """
    ui.label(message).classes("text-body2 text-grey-6")


def _panel(title: str):
    """Open one theme's card.

    The heading stays even though each theme now has a page of its own: it
    is what the tests find a panel by, and it names the theme inside the
    frame rather than only in the title bar.

    Args:
        title: The panel's German heading.

    Returns:
        The card's context manager, already holding the heading.
    """
    card = ui.card().classes("w-full")
    with card:
        ui.label(title).classes("text-lg font-bold")
    return card


def _render_energy_panel(legs, latest_reading) -> None:
    """Draw the Energie panel: flow, shared energy and self-consumption.

    Args:
        legs: Every LEG, for the filter.
        latest_reading: ISO timestamp of the newest reading, or `None`.

    Returns:
        None.
    """
    has_readings = latest_reading is not None
    with _panel("Energie"):
        if not has_readings:
            _empty_note("Noch keine Messdaten importiert.")

        leg_select = ui.select(
            {None: "Alle LEGs", **{leg.id: leg.name for leg in legs}}, value=None, label="LEG"
        ).classes("w-64")
        # Opened on the newest reading rather than on today: an import
        # usually lands a completed quarter, so "now" is routinely a window
        # with nothing in it -- and an empty chart on arrival reads as a
        # broken page, not as an empty period.
        axis = render_time_axis(
            _ENERGY_GRANULARITIES,
            lambda: refresh(),
            anchor=datetime.fromisoformat(latest_reading) if latest_reading else None,
        )
        empty_note = ui.label("").classes("text-body2 text-orange-9")
        chart = ui.echart({}).classes("w-full").style(_CHART_HEIGHT)

        def refresh() -> None:
            """Reload the chart for the current LEG, window and resolution.

            Returns:
                None.
            """
            with connection_scope() as connection:
                series = energy_series(connection, axis.key, axis.window, leg_id=leg_select.value)
                # The same window a year earlier, bucket for bucket, so the
                # two lines line up on one axis. Drawn only when there is
                # something there: an empty comparison line would suggest
                # last year was a bad year rather than an absent one.
                prior = energy_series(
                    connection,
                    axis.key,
                    shift_window_one_year(axis.window),
                    leg_id=leg_select.value,
                )
            # Readings exist, but not here: a different statement from
            # "nothing imported yet", and the reader needs to tell them
            # apart before reaching for the arrows.
            empty_note.text = (
                "Keine Messdaten in diesem Zeitraum."
                if has_readings and not any(b.consumption_kwh or b.feed_in_kwh for b in series)
                else ""
            )
            values = [bucket.value_for(axis.key) for bucket in series]
            shares = [
                None
                if bucket.self_consumption_share is None
                else round(bucket.self_consumption_share * 100, 1)
                for bucket in series
            ]
            prior_values = [bucket.value_for(axis.key) for bucket in prior]
            has_prior = any(bucket.consumption_kwh or bucket.feed_in_kwh for bucket in prior)
            comparison = (
                [
                    {
                        "name": "Bezug Vorjahr",
                        "type": "line",
                        "data": [v[0] for v in prior_values],
                        "lineStyle": {"type": "dashed", "opacity": 0.5},
                        "itemStyle": {"opacity": 0.4},
                        "symbol": "none",
                    }
                ]
                if has_prior
                else []
            )
            legend = ["Bezug", "Einspeisung", "Lokal geteilt", "Eigenverbrauch"]
            if has_prior:
                legend.append("Bezug Vorjahr")

            chart.options.clear()
            chart.options.update(
                {
                    "tooltip": {"trigger": "axis"},
                    "legend": {"data": legend},
                    "xAxis": {"type": "category", "data": axis.axis_labels()},
                    "yAxis": [
                        {"type": "value", "name": energy_unit(axis.key)},
                        {"type": "value", "name": "%", "min": 0, "max": 100, "position": "right"},
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
                        *comparison,
                    ],
                }
            )
            chart.update()

        leg_select.on_value_change(lambda _: refresh())
        refresh()


def _render_people_panel() -> None:
    """Draw the Personen panel: where the pipeline waits, and how it grew.

    Returns:
        None.
    """
    with connection_scope() as connection:
        steps, completed = onboarding_funnel(connection)
        growth = monthly_growth_counts(connection, months=_MONTHS_SHOWN)

    with _panel("Personen"):
        if not any(step.waiting for step in steps) and not completed:
            _empty_note("Noch keine Aufnahmen erfasst.")
        else:
            # Horizontal bars, because the step names are long: upright
            # they would be unreadable at any sensible chart height.
            ui.echart(
                {
                    "tooltip": {"trigger": "axis"},
                    "grid": {"left": 220, "top": 10, "bottom": 30},
                    "xAxis": {"type": "value", "name": "Personen"},
                    "yAxis": {
                        "type": "category",
                        # ECharts puts index 0 at the **bottom** of a
                        # category axis, so the data runs bottom-up: the
                        # finished ones lowest, then the steps in reverse,
                        # which reads top-down as the process itself runs.
                        "data": ["abgeschlossen"] + [step.label for step in reversed(steps)],
                    },
                    "series": [
                        {
                            "name": "Personen",
                            "type": "bar",
                            "data": [completed] + [step.waiting for step in reversed(steps)],
                        }
                    ],
                }
            ).classes("w-full").style(_CHART_HEIGHT)

        ui.label("Wachstum").classes("text-body1 font-bold mt-4")
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
        ).classes("w-full").style(_CHART_HEIGHT)


def _render_money_panel() -> None:
    """Draw the Debitoren panel: invoiced, received, and what stays open.

    Returns:
        None.
    """
    with connection_scope() as connection:
        has_runs = connection.execute("SELECT 1 FROM billing_runs LIMIT 1").fetchone() is not None

    with _panel("Debitoren und Rechnungen"):
        if not has_runs:
            _empty_note("Noch kein Abrechnungslauf erstellt.")

        axis = render_time_axis(_MONEY_GRANULARITIES, lambda: refresh(), default=GRANULARITY_MONTH)
        chart = ui.echart({}).classes("w-full").style(_CHART_HEIGHT)

        def refresh() -> None:
            """Reload the receivables chart for the current window.

            Returns:
                None.
            """
            with connection_scope() as connection:
                series = receivables_series(connection, axis.key, axis.window)
            chart.options.clear()
            chart.options.update(
                {
                    "tooltip": {"trigger": "axis"},
                    "legend": {"data": ["Verrechnet", "Eingegangen", "Offen"]},
                    "xAxis": {"type": "category", "data": axis.axis_labels()},
                    "yAxis": {"type": "value", "name": "CHF"},
                    "series": [
                        {"name": "Verrechnet", "type": "bar", "data": [b.invoiced_chf for b in series]},
                        {"name": "Eingegangen", "type": "bar", "data": [b.received_chf for b in series]},
                        # A line, because it is a running total rather than
                        # something that happened in that bucket.
                        {"name": "Offen", "type": "line", "data": [b.open_chf for b in series]},
                    ],
                }
            )
            chart.update()

        refresh()


def _render_distribution_panel() -> None:
    """Draw the Verteilung view: how the deployment sits across the LEGs.

    Two pies and a table, replacing a single chart that carried stacked
    metering-point bars and a PV-capacity line on a second axis: three
    quantities, two units, seven categories, and no clear question.

    A pie is the right instrument for "which LEG is how big", because that
    is a share of a whole. It is the wrong one for comparing similar
    slices -- two LEGs hold 13 metering points each here, and no pie will
    ever show that they are equal -- so the exact figures, including the
    direction split a pie cannot express at all, sit in the table
    underneath. Chart for the shape, table for the numbers.

    Returns:
        None.
    """
    with connection_scope() as connection:
        distributions = distribution_by_leg(connection)

    with _panel("Verteilung auf die LEGs"):
        if not distributions:
            _empty_note("Noch keine LEG erfasst.")
            return

        with_meters = [d for d in distributions if d.metering_points]
        with_pv = [d for d in distributions if d.pv_kwp]

        with ui.row().classes("w-full gap-4 items-stretch flex-wrap"):
            _pie(
                "Messpunkte",
                [{"name": d.name, "value": d.metering_points} for d in with_meters],
                "Noch keine Messpunkte zugewiesen.",
            )
            _pie(
                "PV-Leistung (kWp)",
                [{"name": d.name, "value": d.pv_kwp} for d in with_pv],
                "Noch keine PV-Leistung erfasst.",
            )

        ui.table(
            columns=[
                {"name": "name", "label": "LEG", "field": "name", "align": "left"},
                {"name": "consumption", "label": "Bezug", "field": "consumption", "align": "right"},
                {"name": "feed_in", "label": "Einspeisung", "field": "feed_in", "align": "right"},
                {"name": "total", "label": "Messpunkte", "field": "total", "align": "right"},
                {"name": "pv", "label": "kWp", "field": "pv", "align": "right"},
            ],
            rows=[
                {
                    "name": d.name,
                    "consumption": d.consumption_metering_points,
                    "feed_in": d.feed_in_metering_points,
                    "total": d.metering_points,
                    "pv": _format_capacity(d.pv_kwp),
                }
                for d in distributions
            ],
            row_key="name",
        ).classes("w-full mt-4").props("dense")


def _render_balance_panel() -> None:
    """Draw the Ausgewogenheit view: how each LEG's two sides compare.

    Two readings of one question, in the order they become available. The
    bars compare the meter counts, which exist as soon as a LEG does. The
    table adds the measured share of production that actually found a taker,
    which needs an import and is the figure that really answers "is this LEG
    well matched" -- a meter count says nothing about whether the sun shone
    while anybody was drawing.

    The app states both and grades neither. It once recommended moving
    people between LEGs, and that was removed because presence is not
    viability (see `app.domain.participant_mix`); a threshold for "good"
    would be the same mistake wearing a percentage sign, since the decision
    turns on economics, on what the participants agree to and on what BKW
    confirms per location. So the LEGs are ordered on one continuum, from
    production-heavy to consumption-heavy, and both extremes are where the
    eye lands first.

    Returns:
        None.
    """
    with connection_scope() as connection:
        balances = leg_balance(connection)

    with _panel("Ausgewogenheit der LEGs"):
        if not balances:
            _empty_note("Noch keine LEG erfasst.")
            return

        populated = [balance for balance in balances if balance.metering_points]
        if not populated:
            _empty_note("Noch keine Messpunkte einer LEG zugewiesen.")
            return

        # Reversed: an ECharts category axis puts index 0 at the bottom, so
        # the list has to be turned around for the production-heavy LEG to
        # appear at the top -- the same order the table below reads in.
        for_chart = list(reversed(populated))
        ui.echart(
            {
                "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                "legend": {"data": ["Produzenten", "Konsumenten"]},
                "grid": {"left": "3%", "right": "4%", "bottom": "3%", "containLabel": True},
                "xAxis": {"type": "value", "name": "Messpunkte", "minInterval": 1},
                "yAxis": {"type": "category", "data": [b.name for b in for_chart]},
                "series": [
                    {
                        "name": "Produzenten",
                        "type": "bar",
                        "data": [b.producer_metering_points for b in for_chart],
                    },
                    {
                        "name": "Konsumenten",
                        "type": "bar",
                        "data": [b.consumer_metering_points for b in for_chart],
                    },
                ],
            }
        ).classes("w-full").style(_CHART_HEIGHT)

        ui.table(
            columns=[
                {"name": "name", "label": "LEG", "field": "name", "align": "left"},
                {"name": "producers", "label": "Produzenten", "field": "producers", "align": "right"},
                {"name": "consumers", "label": "Konsumenten", "field": "consumers", "align": "right"},
                {"name": "ratio", "label": "Produzenten je Konsument", "field": "ratio", "align": "right"},
                {"name": "shared", "label": "Geteilt von Produktion", "field": "shared", "align": "right"},
                {"name": "coverage", "label": "Lokal gedeckter Bezug", "field": "coverage", "align": "right"},
            ],
            rows=[_balance_row(balance) for balance in balances],
            row_key="name",
        ).classes("w-full mt-4").props("dense")

        if any(balance.has_readings for balance in balances):
            _empty_note("Energiewerte über alle importierten Messwerte.")
        else:
            _empty_note("Noch keine Messdaten importiert — nur die Messpunkte sind auswertbar.")


def _balance_row(balance) -> dict:
    """Build one row of the Ausgewogenheit table.

    Args:
        balance: One `app.domain.statistics.LegBalance`.

    Returns:
        The row dict the table renders.
    """
    if not balance.metering_points:
        ratio_text = "keine Messpunkte"
    elif balance.one_sided_note:
        # A missing side is the more useful statement than the quotient it
        # produces -- "nur Konsumenten" says more than "0,0", and with no
        # consumers there is no quotient to print at all.
        ratio_text = balance.one_sided_note
    else:
        ratio_text = format_factor(balance.producers_per_consumer)

    return {
        "name": balance.name,
        "producers": balance.producer_metering_points,
        "consumers": balance.consumer_metering_points,
        "ratio": ratio_text,
        "shared": _optional_percent(balance.shared_share_of_production),
        "coverage": _optional_percent(balance.local_coverage),
    }


def _optional_percent(value) -> str:
    """Format a percentage that may not exist yet.

    Args:
        value: The percentage, or `None` when its denominator was zero.

    Returns:
        The German-formatted percentage, or an em dash. Deliberately not
        "0 %": nothing fed in and nothing shared of what was fed in are
        different statements, and printing a zero would assert the second.
    """
    return "—" if value is None else format_percent(value)


def _pie(title: str, data: list[dict], empty_message: str) -> None:
    """Draw one share-of-whole pie, or say why there is none.

    Args:
        title: The pie's German heading.
        data: `{"name", "value"}` entries; empty draws the note instead.
        empty_message: What to say when there is nothing to divide up.

    Returns:
        None.
    """
    with ui.column().classes("flex-grow min-w-[320px] gap-0"):
        ui.label(title).classes("text-body1 font-bold")
        if not data:
            _empty_note(empty_message)
            return
        ui.echart(
            {
                "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
                "series": [
                    {
                        "type": "pie",
                        # A donut rather than a full circle: the hole stops
                        # the eye trying to judge angles at the centre,
                        # which is what a pie is worst at.
                        "radius": ["40%", "72%"],
                        "data": data,
                        # No labels on the slices. LEG names are long
                        # enough that ECharts truncated them to "LEG-Itti…",
                        # which is worse than no label at all: it takes the
                        # space and still says nothing. Hovering gives the
                        # full name, value and share, and the table below
                        # has every name spelled out.
                        "label": {"show": False},
                        "labelLine": {"show": False},
                    }
                ],
            }
        ).classes("w-full").style(_CHART_HEIGHT)


def _format_capacity(value: float) -> str:
    """Format a kWp figure the way a German reader writes it.

    Args:
        value: The figure.

    Returns:
        Two decimals with a comma, matching the overview's tiles.
    """
    return f"{value:.2f}".replace(".", ",")


@ui.page("/statistics")
def statistics_page() -> None:
    """Send the old single-page route to the energy view.

    Kept rather than deleted: it was the one Statistik route for the
    app's whole life so far, and a dead link is a worse answer than a
    redirect for something that costs one line.

    Returns:
        None.
    """
    ui.navigate.to("/statistics/energy")


@ui.page("/statistics/energy")
def statistics_energy_page() -> None:
    """Render the Energie view: flow, shared energy, self-consumption.

    Returns:
        None.
    """
    with page_frame("/statistics/energy", "Statistik: Energie"):
        with connection_scope() as connection:
            legs = leg_repo.list_all(connection)
            latest_reading = connection.execute("SELECT MAX(timestamp) FROM readings").fetchone()[0]
        _render_energy_panel(legs, latest_reading)


@ui.page("/statistics/growth")
def statistics_growth_page() -> None:
    """Render the Wachstum view: where the pipeline waits, and how it grew.

    Returns:
        None.
    """
    with page_frame("/statistics/growth", "Statistik: Wachstum"):
        _render_people_panel()


@ui.page("/statistics/receivables")
def statistics_receivables_page() -> None:
    """Render the Debitorenverlauf view: invoiced, received, still open.

    Returns:
        None.
    """
    with page_frame("/statistics/receivables", "Statistik: Debitorenverlauf"):
        _render_money_panel()


@ui.page("/statistics/distribution")
def statistics_distribution_page() -> None:
    """Render the Verteilung view: how big each LEG is.

    Returns:
        None.
    """
    with page_frame("/statistics/distribution", "Statistik: Verteilung"):
        _render_distribution_panel()


@ui.page("/statistics/balance")
def statistics_balance_page() -> None:
    """Render the Ausgewogenheit view: how each LEG's two sides compare.

    A page of its own rather than a fifth card on Verteilung: that view
    answers how *big* each LEG is, this one whether each LEG is *matched*,
    and stacking two charts made the one you wanted the one you had to
    scroll past.

    Returns:
        None.
    """
    with page_frame("/statistics/balance", "Statistik: Ausgewogenheit"):
        _render_balance_panel()
