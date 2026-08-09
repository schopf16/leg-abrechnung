"""Billing run page: compute, inspect and re-run quarterly billing, per LEG."""

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.billing import create_or_replace_billing_run
from app.domain.distribution import LegNotAssignedError
from app.domain.period import list_available_periods
from app.gui.navigation import page_frame
from app.gui.period_selector import build_period_selector
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.pdf.export_service import export_billing_run_documents


def _type_label(item) -> str:
    """German label for a billing item's net direction.

    Args:
        item: A `BillingRunItem`.

    Returns:
        "Rechnung" if the person owes the LEG, "Gutschrift" if the
        LEG owes the person, "Ausgeglichen" if the net is zero.
    """
    if item.is_owed_to_leg:
        return "Rechnung"
    if item.is_owed_by_leg:
        return "Gutschrift"
    return "Ausgeglichen"


@ui.page("/abrechnung")
def abrechnung_page() -> None:
    """Render the billing run page.

    Returns:
        None.
    """
    with page_frame("/abrechnung", "Rechnungslauf"):
        ui.label(
            "Berechnet die lokale Verteilung und erzeugt Rechnungen/"
            "Gutschriften je Person für ein Quartal, innerhalb einer LEG. "
            "Ein erneuter Lauf für dieselbe LEG und dasselbe Quartal "
            "ersetzt den vorherigen vollständig."
        ).classes("text-body2 text-grey-8")

        with connection_scope() as connection:
            available_periods = list_available_periods(connection)
            legs = leg_repo.list_all(connection)

        if not available_periods:
            ui.label(
                "⚠ Noch keine Messdaten vorhanden. Bitte zuerst auf der "
                "Seite „Import“ Daten einlesen."
            ).classes("text-negative mt-2")
            return

        if not legs:
            ui.label(
                "⚠ Noch keine LEG angelegt. Bitte zuerst unter „LEGs“ "
                "mindestens eine LEG erfassen."
            ).classes("text-negative mt-2")
            return

        leg_options = {leg.id: leg.name for leg in legs}

        with ui.row().classes("items-end gap-2"):
            leg_select = ui.select(leg_options, label="LEG", value=legs[0].id).classes("w-64")
            selector = build_period_selector(available_periods)
            run_button = ui.button("Abrechnung erstellen / neu berechnen")

        result_column = ui.column().classes("w-full mt-4")

        runs_table = ui.table(
            columns=[
                {"name": "leg", "label": "LEG", "field": "leg", "align": "left"},
                {"name": "period", "label": "Quartal", "field": "period", "align": "left"},
                {"name": "created_at", "label": "Erstellt am", "field": "created_at", "align": "left"},
                {"name": "price", "label": "Preis (Rp./kWh)", "field": "price", "align": "right"},
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
            ],
            rows=[],
            row_key="id",
        ).classes("w-full mt-6")

        def refresh_runs_table() -> None:
            """Reload the list of past billing runs, across all LEGs.

            Returns:
                None.
            """
            with connection_scope() as connection:
                runs = billing_run_repo.list_runs(connection)
                leg_names = {leg.id: leg.name for leg in leg_repo.list_all(connection)}
            runs_table.rows = [
                {
                    "id": r.id,
                    "leg": leg_names.get(r.leg_id, "?"),
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
                person_names = {
                    p.id: p.anzeige_name for p in person_repo.list_all(connection)
                }
                leg_name = leg_options.get(run.leg_id, "?")

            result_column.clear()
            with result_column:
                with ui.card().classes("w-full"):
                    ui.label(f"{leg_name} -- Q{run.period_quarter} {run.period_year}").classes(
                        "text-lg font-bold"
                    )
                    ui.label(
                        f"{distribution.interval_count} Intervalle verarbeitet, "
                        f"{len(items)} Belege."
                    )
                    if distribution.unassigned_kwh:
                        ui.label(
                            f"⚠ {distribution.unassigned_kwh} kWh konnten keiner "
                            "Person zugeordnet werden (Lücke in den "
                            "Zuordnungen -- siehe Auswertungen)."
                        ).classes("text-negative")

                    balance_text = (
                        "✓ Summenabgleich OK (Rechnungen ≈ Gutschriften)"
                        if control_check.balanced
                        else "⚠ Summenabgleich weicht ab!"
                    )
                    balance_class = "text-positive" if control_check.balanced else "text-negative"
                    ui.label(
                        f"{balance_text} "
                        f"(offen zugunsten LEG: {control_check.total_owed_to_leg_rappen / 100:.2f} CHF, "
                        f"offen zulasten LEG: {control_check.total_owed_by_leg_rappen / 100:.2f} CHF)"
                    ).classes(balance_class)

                if items:
                    ui.table(
                        columns=[
                            {"name": "person", "label": "Person", "field": "person", "align": "left"},
                            {"name": "typ", "label": "Typ", "field": "typ", "align": "left"},
                            {"name": "consumed", "label": "Bezug (kWh)", "field": "consumed", "align": "right"},
                            {"name": "produced", "label": "Vergütung (kWh)", "field": "produced", "align": "right"},
                            {"name": "amount", "label": "Netto-Betrag (CHF)", "field": "amount", "align": "right"},
                        ],
                        rows=[
                            {
                                "person": person_names.get(i.person_id, "?"),
                                "typ": _type_label(i),
                                "consumed": f"{i.consumed_kwh:.3f}",
                                "produced": f"{i.produced_kwh:.3f}",
                                "amount": f"{i.net_amount_chf:.2f}",
                            }
                            for i in items
                        ],
                        row_key="person",
                    ).classes("w-full mt-2")
                else:
                    ui.label("Keine Belege für dieses Quartal (keine lokale Verteilung).")

                if items:
                    ui.button(
                        "PDFs + CSV-Listen erzeugen (1 Abrechnung je Person, "
                        "Rechnungs- und Auszahlungsliste)",
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
                    if export_result.invoice_list_path:
                        ui.label(f"Rechnungsliste (CSV): {export_result.invoice_list_path.name}")
                    if export_result.payout_list_path:
                        ui.label(f"Auszahlungsliste (CSV): {export_result.payout_list_path.name}")
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
            """Compute (or recompute) the billing run for the selected LEG and quarter.

            Returns:
                None.
            """
            if leg_select.value is None:
                ui.notify("Bitte eine LEG wählen.", type="warning")
                return
            period = selector.selected_period
            if period is None:
                ui.notify("Bitte Jahr und Quartal wählen.", type="warning")
                return
            year, quarter = period
            try:
                with connection_scope() as connection:
                    run, items, control_check, distribution = create_or_replace_billing_run(
                        connection, leg_select.value, year, quarter
                    )
            except LegNotAssignedError as exc:
                result_column.clear()
                with result_column:
                    ui.label(f"⚠ {exc}").classes("text-negative")
                ui.notify("Abrechnung nicht möglich: LEG-Zuweisung fehlt.", type="negative")
                return
            render_result(run, items, control_check, distribution)
            refresh_runs_table()
            ui.notify(
                f"Abrechnung für {leg_options[leg_select.value]}, Q{quarter} {year} erstellt.",
                type="positive",
            )

        run_button.on_click(run_billing)

        ui.label("Bisherige Läufe").classes("text-lg font-bold mt-6")
        refresh_runs_table()
