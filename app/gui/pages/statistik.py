"""Statistik page: trend charts over the last 12 months.

Two independent views: energy flow (Bezug/Einspeisung/Saldo, optionally
scoped to one LEG) and master-data growth (cumulative Personen/
Messpunkte/Standorte/Trafokreise/LEGs), both aggregated by
`app.domain.statistics`.
"""

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.period import MONTH_NAMES_DE
from app.domain.statistics import monthly_energy_totals, monthly_growth_counts
from app.gui.navigation import page_frame
from app.models import leg as leg_repo

_MONTHS_SHOWN = 12


def _month_label(year: int, month: int) -> str:
    """Format a calendar month as a short chart-axis label.

    Args:
        year: Calendar year.
        month: Calendar month, 1 to 12.

    Returns:
        A label such as "Sep 2025".
    """
    return f"{MONTH_NAMES_DE[month][:3]} {year}"


@ui.page("/statistik")
def statistik_page() -> None:
    """Render the Statistik page with energy-flow and growth charts.

    Returns:
        None.
    """
    with page_frame("/statistik", "Statistik"):
        ui.label(
            f"Entwicklung über die letzten {_MONTHS_SHOWN} Monate -- "
            "unabhängig von Abrechnungsläufen, rein zur Übersicht."
        ).classes("text-body2 text-grey-8")

        with connection_scope() as connection:
            legs = leg_repo.list_all(connection)
        leg_options = {None: "Alle LEGs", **{leg.id: leg.name for leg in legs}}

        ui.label("Energiefluss").classes("text-lg font-bold mt-4")
        leg_select = ui.select(leg_options, value=None, label="LEG").classes("w-64")
        energy_chart = ui.echart({}).classes("w-full").style("height: 350px")

        def refresh_energy_chart() -> None:
            """Reload the energy-flow chart for the currently selected LEG.

            Returns:
                None.
            """
            with connection_scope() as connection:
                monthly = monthly_energy_totals(connection, leg_id=leg_select.value, months=_MONTHS_SHOWN)
            energy_chart.options.clear()
            energy_chart.options.update(
                {
                    "tooltip": {"trigger": "axis"},
                    "legend": {"data": ["Bezug", "Einspeisung", "Saldo"]},
                    "xAxis": {"type": "category", "data": [_month_label(m.year, m.month) for m in monthly]},
                    "yAxis": {"type": "value", "name": "kWh"},
                    "series": [
                        {"name": "Bezug", "type": "bar", "data": [m.bezug_kwh for m in monthly]},
                        {"name": "Einspeisung", "type": "bar", "data": [m.einspeisung_kwh for m in monthly]},
                        {"name": "Saldo", "type": "line", "data": [round(m.saldo_kwh, 3) for m in monthly]},
                    ],
                }
            )
            energy_chart.update()

        leg_select.on_value_change(lambda _: refresh_energy_chart())
        refresh_energy_chart()

        ui.label("Wachstum").classes("text-lg font-bold mt-6")
        ui.label(
            "Kumulierte Anzahl je Monat -- zeigt, wie die Stammdaten über "
            "die Zeit gewachsen sind."
        ).classes("text-body2 text-grey-8")
        with connection_scope() as connection:
            growth = monthly_growth_counts(connection, months=_MONTHS_SHOWN)
        ui.echart(
            {
                "tooltip": {"trigger": "axis"},
                "legend": {"data": ["Personen", "Messpunkte", "Standorte", "Trafokreise", "LEGs"]},
                "xAxis": {"type": "category", "data": [_month_label(g.year, g.month) for g in growth]},
                "yAxis": {"type": "value"},
                "series": [
                    {"name": "Personen", "type": "line", "data": [g.personen for g in growth]},
                    {"name": "Messpunkte", "type": "line", "data": [g.messpunkte for g in growth]},
                    {"name": "Standorte", "type": "line", "data": [g.standorte for g in growth]},
                    {"name": "Trafokreise", "type": "line", "data": [g.trafokreise for g in growth]},
                    {"name": "LEGs", "type": "line", "data": [g.legs for g in growth]},
                ],
            }
        ).classes("w-full").style("height: 350px")
