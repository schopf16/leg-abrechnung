"""Billing run page: compute, inspect and re-run quarterly billing."""

from datetime import date

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.billing import create_or_replace_billing_run
from app.gui.navigation import page_frame
from app.models import billing_run as billing_run_repo
from app.models import participant as participant_repo
from app.pdf.export_service import export_billing_run_documents

KIND_LABELS = {"rechnung": "Rechnung", "gutschrift": "Gutschrift"}


def _current_quarter() -> tuple[int, int]:
    """Determine the calendar quarter before the current one (last completed).

    Used as a sensible default selection: billing usually happens for a
    quarter that has already ended.

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


@ui.page("/abrechnung")
def abrechnung_page() -> None:
    """Render the billing run page.

    Returns:
        None.
    """
    with page_frame("/abrechnung", "Abrechnung"):
        ui.label(
            "Berechnet die lokale Verteilung und erzeugt Rechnungen/"
            "Gutschriften je Teilnehmer für ein Quartal. Ein erneuter Lauf "
            "für dasselbe Quartal ersetzt den vorherigen vollständig."
        ).classes("text-body2 text-grey-8")

        default_year, default_quarter = _current_quarter()
        with ui.row().classes("items-end gap-2"):
            year_input = ui.number("Jahr", value=default_year, format="%.0f").classes("w-28")
            quarter_select = ui.select(
                {1: "Q1 (Jan-Mär)", 2: "Q2 (Apr-Jun)", 3: "Q3 (Jul-Sep)", 4: "Q4 (Okt-Dez)"},
                label="Quartal",
                value=default_quarter,
            ).classes("w-48")
            run_button = ui.button("Abrechnung erstellen / neu berechnen")

        result_column = ui.column().classes("w-full mt-4")

        runs_table = ui.table(
            columns=[
                {"name": "period", "label": "Quartal", "field": "period", "align": "left"},
                {"name": "created_at", "label": "Erstellt am", "field": "created_at", "align": "left"},
                {"name": "price", "label": "Preis (Rp./kWh)", "field": "price", "align": "right"},
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
            ],
            rows=[],
            row_key="period",
        ).classes("w-full mt-6")

        def refresh_runs_table() -> None:
            """Reload the list of past billing runs.

            Returns:
                None.
            """
            with connection_scope() as connection:
                runs = billing_run_repo.list_runs(connection)
            runs_table.rows = [
                {
                    "period": f"Q{r.period_quarter} {r.period_year}",
                    "created_at": r.created_at.replace("T", " ").split(".")[0],
                    "price": r.price_rp_per_kwh,
                    "status": r.status,
                }
                for r in runs
            ]
            runs_table.update()

        def render_result(run, items, control_check, distribution) -> None:
            """Render one billing run's results in the result panel.

            Args:
                run: Persisted `BillingRun`.
                items: Persisted `BillingRunItem` list.
                control_check: `ControlCheckResult` from the balance check.
                distribution: `DistributionResult` for the quarter.

            Returns:
                None.
            """
            with connection_scope() as connection:
                participant_names = {
                    p.id: p.name for p in participant_repo.list_all(connection)
                }

            result_column.clear()
            with result_column:
                with ui.card().classes("w-full"):
                    ui.label(f"Q{run.period_quarter} {run.period_year}").classes(
                        "text-lg font-bold"
                    )
                    ui.label(
                        f"{distribution.interval_count} Intervalle verarbeitet, "
                        f"{len(items)} Belege."
                    )
                    if distribution.unassigned_kwh:
                        ui.label(
                            f"⚠ {distribution.unassigned_kwh} kWh konnten keinem "
                            "Teilnehmer zugeordnet werden (Lücke in den "
                            "Zuordnungen -- siehe Auswertungen)."
                        ).classes("text-negative")

                    balance_text = (
                        "✓ Summenabgleich Rechnungen ↔ Gutschriften OK"
                        if control_check.balanced
                        else "⚠ Summenabgleich weicht ab!"
                    )
                    balance_class = "text-positive" if control_check.balanced else "text-negative"
                    ui.label(
                        f"{balance_text} "
                        f"(Rechnungen: {control_check.total_invoices_rappen / 100:.2f} CHF, "
                        f"Gutschriften: {control_check.total_credits_rappen / 100:.2f} CHF)"
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
                    ui.label("Keine Belege für dieses Quartal (keine lokale Verteilung).")

                if items:
                    ui.button(
                        "PDFs erzeugen (Rechnungen, Gutschriften, Zahlliste)",
                        on_click=lambda: export_documents(run.id),
                    ).classes("mt-4")

        def export_documents(run_id: int) -> None:
            """Generate all PDFs for a billing run and report the outcome.

            Args:
                run_id: Database id of the billing run to export.

            Returns:
                None.
            """
            with connection_scope() as connection:
                run = billing_run_repo.get_run(connection, run_id)
                export_result = export_billing_run_documents(connection, run)

            with result_column:
                with ui.card().classes("w-full mt-2"):
                    ui.label(f"Dokumente gespeichert in: {export_result.output_dir}").classes(
                        "font-bold"
                    )
                    ui.label(f"{len(export_result.document_paths)} Belege erzeugt.")
                    if export_result.payment_list_path:
                        ui.label(f"Zahlliste: {export_result.payment_list_path.name}")
                    for error in export_result.errors:
                        ui.label(f"⚠ {error}").classes("text-negative")

            if export_result.errors:
                ui.notify(
                    f"{len(export_result.errors)} Beleg(e) konnten nicht erzeugt werden.",
                    type="warning",
                )
            else:
                ui.notify("PDFs erfolgreich erzeugt.", type="positive")

        def run_billing() -> None:
            """Compute (or recompute) the billing run for the selected quarter.

            Returns:
                None.
            """
            year = int(year_input.value)
            quarter = int(quarter_select.value)
            with connection_scope() as connection:
                run, items, control_check, distribution = create_or_replace_billing_run(
                    connection, year, quarter
                )
            render_result(run, items, control_check, distribution)
            refresh_runs_table()
            ui.notify(f"Abrechnung für Q{quarter} {year} erstellt.", type="positive")

        run_button.on_click(run_billing)

        ui.label("Bisherige Läufe").classes("text-lg font-bold mt-6")
        refresh_runs_table()
