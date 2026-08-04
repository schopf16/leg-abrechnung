"""Reports page: per-participant quarterly overview and plausibility checks."""

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.billing import compute_billing_items, verify_sum_balance
from app.domain.distribution import compute_quarter_distribution
from app.domain.period import list_available_periods
from app.domain.quality_checks import check_assignment_consistency, check_reading_completeness
from app.gui.navigation import page_frame
from app.gui.period_selector import build_period_selector
from app.models import participant as participant_repo
from app.models import settings as settings_repo

CATEGORY_LABELS = {
    "zuordnung_ueberlappung": "Überlappende Zuordnung",
    "zuordnung_luecke": "Lücke in Zuordnung",
    "messdaten_luecke": "Lücke in Messdaten",
}


def _type_label(item) -> str:
    """German label for a billing item's net direction.

    Args:
        item: A `BillingRunItem`.

    Returns:
        "Rechnung" if the participant owes the LEG, "Gutschrift" if the
        LEG owes the participant, "Ausgeglichen" if the net is zero.
    """
    if item.is_owed_to_leg:
        return "Rechnung"
    if item.is_owed_by_leg:
        return "Gutschrift"
    return "Ausgeglichen"


@ui.page("/auswertungen")
def auswertungen_page() -> None:
    """Render the reports and plausibility-check page.

    Returns:
        None.
    """
    with page_frame("/auswertungen", "Auswertungen"):
        ui.label(
            "Übersicht je Teilnehmer für ein Quartal (auf Basis der aktuellen "
            "Messdaten und des aktuellen Preises -- unabhängig davon, ob "
            "bereits eine Abrechnung erstellt wurde) sowie Plausibilitäts- "
            "prüfungen über die gesamten Stammdaten."
        ).classes("text-body2 text-grey-8")

        with connection_scope() as connection:
            available_periods = list_available_periods(connection)

        if not available_periods:
            ui.label(
                "⚠ Noch keine Messdaten vorhanden. Bitte zuerst auf der "
                "Seite „Import“ Daten einlesen."
            ).classes("text-negative mt-2")
            return

        with ui.row().classes("items-end gap-2"):
            selector = build_period_selector(available_periods)
            check_button = ui.button("Auswerten")

        overview_column = ui.column().classes("w-full mt-4")
        warnings_column = ui.column().classes("w-full mt-6")

        def run_checks() -> None:
            """Compute the quarterly overview and run all plausibility checks.

            Returns:
                None.
            """
            period = selector.selected_period
            if period is None:
                ui.notify("Bitte Jahr und Quartal wählen.", type="warning")
                return
            year, quarter = period

            with connection_scope() as connection:
                price = settings_repo.get_settings(connection).price_rp_per_kwh
                distribution = compute_quarter_distribution(connection, year, quarter)
                items = compute_billing_items(distribution, price)
                control_check = verify_sum_balance(items)
                participant_names = {
                    p.id: p.name for p in participant_repo.list_all(connection)
                }
                assignment_warnings = check_assignment_consistency(connection)
                completeness_warnings = check_reading_completeness(connection, year, quarter)

            overview_column.clear()
            with overview_column:
                with ui.card().classes("w-full"):
                    ui.label(f"Übersicht Q{quarter} {year}").classes("text-lg font-bold")
                    ui.label(
                        f"{distribution.interval_count} Intervalle, "
                        f"{len(distribution.participant_results)} Teilnehmer mit lokalem Anteil."
                    )
                    if distribution.unassigned_kwh:
                        ui.label(
                            f"⚠ {distribution.unassigned_kwh} kWh ohne zugeordneten Teilnehmer."
                        ).classes("text-negative")
                    balance_class = "text-positive" if control_check.balanced else "text-negative"
                    balance_symbol = "✓" if control_check.balanced else "⚠"
                    ui.label(
                        f"{balance_symbol} Summenabgleich: offen zugunsten LEG "
                        f"{control_check.total_owed_to_leg_rappen / 100:.2f} CHF, offen "
                        f"zulasten LEG {control_check.total_owed_by_leg_rappen / 100:.2f} CHF"
                    ).classes(balance_class)

                if items:
                    ui.table(
                        columns=[
                            {"name": "participant", "label": "Teilnehmer", "field": "participant", "align": "left"},
                            {"name": "typ", "label": "Typ", "field": "typ", "align": "left"},
                            {"name": "consumed", "label": "Bezug (kWh)", "field": "consumed", "align": "right"},
                            {"name": "produced", "label": "Vergütung (kWh)", "field": "produced", "align": "right"},
                            {"name": "amount", "label": "Netto-Betrag (CHF)", "field": "amount", "align": "right"},
                        ],
                        rows=[
                            {
                                "participant": participant_names.get(i.participant_id, "?"),
                                "typ": _type_label(i),
                                "consumed": f"{i.consumed_kwh:.3f}",
                                "produced": f"{i.produced_kwh:.3f}",
                                "amount": f"{i.net_amount_chf:.2f}",
                            }
                            for i in items
                        ],
                        row_key="participant",
                    ).classes("w-full mt-2")
                else:
                    ui.label("Keine lokale Verteilung für dieses Quartal.")

            warnings_column.clear()
            with warnings_column:
                ui.label("Plausibilitätsprüfungen").classes("text-lg font-bold")
                all_warnings = assignment_warnings + completeness_warnings
                if not all_warnings:
                    ui.label("✓ Keine Auffälligkeiten gefunden.").classes("text-positive")
                else:
                    ui.table(
                        columns=[
                            {"name": "category", "label": "Kategorie", "field": "category", "align": "left"},
                            {"name": "message", "label": "Meldung", "field": "message", "align": "left"},
                        ],
                        rows=[
                            {
                                "category": CATEGORY_LABELS.get(w.category, w.category),
                                "message": w.message,
                            }
                            for w in all_warnings
                        ],
                        row_key="message",
                    ).classes("w-full")

        check_button.on_click(run_checks)
        run_checks()
