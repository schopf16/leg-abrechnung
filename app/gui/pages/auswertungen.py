"""Reports page: per-participant quarterly overview and plausibility checks."""

from datetime import date

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.billing import compute_billing_items, verify_sum_balance
from app.domain.distribution import compute_quarter_distribution
from app.domain.quality_checks import check_assignment_consistency, check_reading_completeness
from app.gui.navigation import page_frame
from app.models import participant as participant_repo
from app.models import settings as settings_repo

KIND_LABELS = {"rechnung": "Rechnung", "gutschrift": "Gutschrift"}
CATEGORY_LABELS = {
    "zuordnung_ueberlappung": "Überlappende Zuordnung",
    "zuordnung_luecke": "Lücke in Zuordnung",
    "messdaten_luecke": "Lücke in Messdaten",
}


def _default_quarter() -> tuple[int, int]:
    """Determine the calendar quarter before the current one.

    Returns:
        A `(year, quarter)` tuple.
    """
    today = date.today()
    quarter = (today.month - 1) // 3 + 1
    quarter -= 1
    year = today.year
    if quarter == 0:
        quarter = 4
        year -= 1
    return year, quarter


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

        default_year, default_quarter = _default_quarter()
        with ui.row().classes("items-end gap-2"):
            year_input = ui.number("Jahr", value=default_year, format="%.0f").classes("w-28")
            quarter_select = ui.select(
                {1: "Q1 (Jan-Mär)", 2: "Q2 (Apr-Jun)", 3: "Q3 (Jul-Sep)", 4: "Q4 (Okt-Dez)"},
                label="Quartal",
                value=default_quarter,
            ).classes("w-48")
            check_button = ui.button("Auswerten")

        overview_column = ui.column().classes("w-full mt-4")
        warnings_column = ui.column().classes("w-full mt-6")

        def run_checks() -> None:
            """Compute the quarterly overview and run all plausibility checks.

            Returns:
                None.
            """
            year = int(year_input.value)
            quarter = int(quarter_select.value)

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
                        f"{balance_symbol} Summenabgleich: Rechnungen "
                        f"{control_check.total_invoices_rappen / 100:.2f} CHF, Gutschriften "
                        f"{control_check.total_credits_rappen / 100:.2f} CHF"
                    ).classes(balance_class)

                if items:
                    ui.table(
                        columns=[
                            {"name": "participant", "label": "Teilnehmer", "field": "participant", "align": "left"},
                            {"name": "kind", "label": "Typ", "field": "kind", "align": "left"},
                            {"name": "kwh", "label": "kWh", "field": "kwh", "align": "right"},
                            {"name": "amount", "label": "Betrag (CHF)", "field": "amount", "align": "right"},
                        ],
                        rows=[
                            {
                                "participant": participant_names.get(i.participant_id, "?"),
                                "kind": KIND_LABELS.get(i.kind, i.kind),
                                "kwh": f"{i.kwh:.3f}",
                                "amount": f"{i.amount_chf:.2f}",
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
