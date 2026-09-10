"""Billing run page: compute, inspect and re-run quarterly billing, per LEG."""

from nicegui import ui

from app.config import ConfigError, get_graph_config
from app.db.connection import connection_scope
from app.domain.billing import create_or_replace_billing_run
from app.domain.distribution import LegNotAssignedError
from app.domain.period import list_available_periods
from app.emailing import bulk_send, graph_client
from app.emailing.templates import (
    PERSON_PLACEHOLDERS,
    find_invalid_email_addresses,
    find_unknown_placeholders,
)
from app.gui.navigation import page_frame
from app.gui.period_selector import build_period_selector
from app.gui.safe_notify import safe_notify
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.pdf.export_service import export_billing_run_documents

#: Every placeholder valid in an invoice email -- Person fields plus the
#: billing-context ones (see `app.emailing.bulk_send.INVOICE_EXTRA_PLACEHOLDERS`).
_INVOICE_PLACEHOLDER_KEYS = {*PERSON_PLACEHOLDERS, *bulk_send.INVOICE_EXTRA_PLACEHOLDERS}
_INVOICE_PLACEHOLDER_HINT = ", ".join(f"{{{name}}}" for name in _INVOICE_PLACEHOLDER_KEYS)


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
            leg_select = ui.select(leg_options, label="LEG", value=None).classes("w-64")
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

        #: Cache of the last-rendered run's `control_check`/`distribution`
        #: (see `refresh_current_result`) -- these never change from
        #: sending/resending an invoice email, only `items` (specifically
        #: `email_sent_at`) does, so a post-send refresh must re-fetch
        #: just the items rather than recomputing the whole billing run
        #: (which would delete and recreate every item, wiping
        #: `pdf_path`/`email_sent_at` -- see `create_or_replace_billing_run`).
        current_result_state: dict = {}

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
            current_result_state.update(
                run=run, control_check=control_check, distribution=distribution
            )
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
                    items_table = ui.table(
                        columns=[
                            {"name": "person", "label": "Person", "field": "person", "align": "left"},
                            {"name": "typ", "label": "Typ", "field": "typ", "align": "left"},
                            {"name": "consumed", "label": "Bezug (kWh)", "field": "consumed", "align": "right"},
                            {"name": "produced", "label": "Vergütung (kWh)", "field": "produced", "align": "right"},
                            {"name": "amount", "label": "Netto-Betrag (CHF)", "field": "amount", "align": "right"},
                            {"name": "email", "label": "E-Mail", "field": "email", "align": "left"},
                            {"name": "actions", "label": "", "field": "actions", "align": "right"},
                        ],
                        rows=[
                            {
                                "item_id": i.id,
                                "person": person_names.get(i.person_id, "?"),
                                "typ": _type_label(i),
                                "consumed": f"{i.consumed_kwh:.3f}",
                                "produced": f"{i.produced_kwh:.3f}",
                                "amount": f"{i.net_amount_chf:.2f}",
                                "email": f"gesendet {i.email_sent_at[:10]}" if i.email_sent_at else "-",
                                "can_resend": bool(i.email_sent_at),
                            }
                            for i in items
                        ],
                        row_key="item_id",
                    ).classes("w-full mt-2")
                    items_table.add_slot(
                        "body-cell-actions",
                        r'''
                        <q-td :props="props">
                            <q-btn v-if="props.row.can_resend" dense flat icon="send"
                                   @click="() => $parent.$emit('resend', props.row)">
                                <q-tooltip>Erneut senden</q-tooltip>
                            </q-btn>
                        </q-td>
                        ''',
                    )
                    items_table.on(
                        "resend",
                        lambda event: on_resend_click(run, event.args["item_id"]),
                    )
                else:
                    ui.label("Keine Belege für dieses Quartal (keine lokale Verteilung).")

                if items:
                    with ui.row().classes("gap-2 mt-4"):
                        ui.button(
                            "PDFs + CSV-Listen erzeugen (1 Abrechnung je Person, "
                            "Rechnungs- und Auszahlungsliste)",
                            on_click=lambda: export_documents(run.id),
                        )
                        ui.button(
                            "Rechnungen per E-Mail versenden",
                            on_click=lambda: open_invoice_email_dialog(run),
                        ).props("outline")

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
                    ui.link("→ Debitoren (Zahlungen zuordnen)", "/debitoren")
                    for error in export_result.errors:
                        ui.label(f"⚠ {error}").classes("text-negative")

            if export_result.errors:
                ui.notify(
                    f"{len(export_result.errors)} Beleg(e) konnten nicht erzeugt werden.",
                    type="warning",
                )
            else:
                ui.notify("PDFs erfolgreich erzeugt.", type="positive")

        def refresh_current_result() -> None:
            """Re-render the currently shown result with freshly loaded items.

            Used after sending/resending invoice emails: re-fetches
            `BillingRunItem`s (to pick up the new `email_sent_at`) without
            recomputing the billing run itself -- see `current_result_state`'s
            comment for why that distinction matters.

            Returns:
                None.
            """
            if not current_result_state:
                return
            run = current_result_state["run"]
            with connection_scope() as connection:
                fresh_items = billing_run_repo.list_items(connection, run.id)
            render_result(
                run, fresh_items,
                current_result_state["control_check"], current_result_state["distribution"],
            )

        def on_resend_click(run, item_id: int) -> None:
            """Table row action: force-resend one already-emailed invoice.

            Args:
                run: The billing run the item belongs to.
                item_id: Primary key of the `BillingRunItem` to resend.

            Returns:
                None.
            """
            with connection_scope() as connection:
                item = next(
                    (i for i in billing_run_repo.list_items(connection, run.id) if i.id == item_id),
                    None,
                )
                person = person_repo.get(connection, item.person_id) if item else None
                settings = settings_repo.get_settings(connection)
            if item is None or person is None:
                safe_notify("Position nicht gefunden.", type="negative")
                return

            with ui.dialog() as confirm, ui.card():
                ui.label(
                    f'Rechnung von "{person.anzeige_name}" wurde bereits am '
                    f"{item.email_sent_at[:10]} per E-Mail gesendet. Trotzdem erneut senden?"
                )
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")

                    async def do_resend() -> None:
                        """Force-resend this one invoice regardless of `email_sent_at`.

                        Returns:
                            None.
                        """
                        try:
                            config = get_graph_config()
                        except ConfigError as exc:
                            confirm.close()
                            safe_notify(str(exc), type="negative")
                            return
                        try:
                            with connection_scope() as connection:
                                await bulk_send.resend_invoice_email(
                                    connection, config, run, item,
                                    settings.rechnung_email_betreff, settings.rechnung_email_text,
                                )
                        except (
                            ValueError, graph_client.GraphAuthError, graph_client.GraphApiError,
                        ) as exc:
                            confirm.close()
                            safe_notify(str(exc), type="negative")
                            return
                        safe_notify("Erneut gesendet.", type="positive")
                        confirm.close()
                        refresh_current_result()

                    ui.button("Trotzdem senden", on_click=do_resend, color="negative")
            confirm.open()

        def open_invoice_email_dialog(run) -> None:
            """Open the "Rechnungen per E-Mail versenden" dialog for one run.

            Args:
                run: The billing run to send invoices for.

            Returns:
                None.
            """
            with connection_scope() as connection:
                settings = settings_repo.get_settings(connection)
                items = billing_run_repo.list_items(connection, run.id)
                persons = {p.id: p for p in person_repo.list_all(connection)}

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
                ui.label(
                    f"Rechnungen per E-Mail versenden -- {leg_options.get(run.leg_id, '?')}, "
                    f"Q{run.period_quarter} {run.period_year}"
                ).classes("text-lg font-bold")
                subject_input = ui.input("Betreff", value=settings.rechnung_email_betreff).classes(
                    "w-full"
                )
                body_textarea = ui.textarea(
                    "Nachricht", value=settings.rechnung_email_text
                ).classes("w-full").props("rows=8")
                ui.label(f"Verfügbare Platzhalter: {_INVOICE_PLACEHOLDER_HINT}").classes(
                    "text-caption text-grey-6"
                )

                info_container = ui.column().classes("w-full mt-2")

                def refresh_info() -> None:
                    """(Re-)compute and show who will/won't get this send.

                    Returns:
                        None.
                    """
                    info_container.clear()
                    eligible_persons = []
                    skip_lines = []
                    for item in items:
                        person = persons.get(item.person_id)
                        reason = bulk_send.invoice_skip_reason(person, item)
                        if reason:
                            name = person.anzeige_name if person else f"Person #{item.person_id}"
                            skip_lines.append(f"{name}: {reason}")
                        else:
                            eligible_persons.append(person)
                    unknown = find_unknown_placeholders(
                        subject_input.value, _INVOICE_PLACEHOLDER_KEYS
                    ) | find_unknown_placeholders(body_textarea.value, _INVOICE_PLACEHOLDER_KEYS)
                    invalid_emails = find_invalid_email_addresses(eligible_persons)
                    with info_container:
                        ui.label(
                            f"Wird an {len(eligible_persons)} von {len(items)} Personen gesendet."
                        ).classes("font-bold")
                        for line in skip_lines:
                            ui.label(f"- {line}").classes("text-caption text-grey-6")
                        if unknown:
                            placeholder_list = ", ".join(f"{{{name}}}" for name in unknown)
                            ui.label(f"⚠ Unbekannte Platzhalter: {placeholder_list}").classes(
                                "text-negative"
                            )
                        for person in invalid_emails:
                            ui.label(
                                f"⚠ {person.anzeige_name}: E-Mail-Adresse ungültig "
                                f"({person.kontakt_email or '-'})"
                            ).classes("text-negative text-body2")

                subject_input.on_value_change(lambda _: refresh_info())
                body_textarea.on_value_change(lambda _: refresh_info())
                refresh_info()

                progress_bar = ui.linear_progress(value=0.0, show_value=False).classes(
                    "w-full mt-2"
                )
                progress_bar.visible = False
                progress_label = ui.label("").classes("text-caption")
                progress_warning = ui.label(
                    "Bitte die App während des Versands nicht schliessen."
                ).classes("text-caption text-warning")
                progress_warning.bind_visibility_from(progress_bar, "visible")

                async def do_send() -> None:
                    """Send invoice emails for this run and report the outcome.

                    Returns:
                        None.
                    """
                    send_button.disable()
                    try:
                        config = get_graph_config()
                    except ConfigError as exc:
                        safe_notify(str(exc), type="negative")
                        send_button.enable()
                        return

                    progress_bar.visible = True
                    progress_bar.value = 0.0

                    def on_progress(done: int, total: int) -> None:
                        progress_bar.value = done / total if total else 1.0
                        progress_label.text = f"{done} von {total} gesendet"

                    try:
                        with connection_scope() as connection:
                            result = await bulk_send.send_invoice_emails(
                                connection, config, run,
                                subject_input.value, body_textarea.value,
                                on_progress=on_progress,
                            )
                    except (graph_client.GraphAuthError, graph_client.GraphApiError) as exc:
                        safe_notify(str(exc), type="negative")
                        send_button.enable()
                        return

                    safe_notify(
                        f"{len(result.sent)} Rechnung(en) gesendet."
                        if not result.errors
                        else f"{len(result.sent)} gesendet, {len(result.errors)} Fehler.",
                        type="positive" if not result.errors else "warning",
                    )
                    for error in result.errors:
                        safe_notify(error, type="negative", timeout=8000)
                    dialog.close()
                    refresh_current_result()

                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    send_button = ui.button("Senden", on_click=do_send)
            dialog.open()

        def run_billing() -> None:
            """Compute (or recompute) the billing run for the selected LEG and quarter.

            Only ever called from `confirm_rates_then_run_billing`, right
            after that dialog's own `dialog.close()` -- every notification
            below therefore needs `safe_notify`, not a plain `ui.notify`,
            see `app.gui.safe_notify`'s module docstring.

            Returns:
                None.
            """
            if leg_select.value is None:
                safe_notify("Bitte eine LEG wählen.", type="warning")
                return
            period = selector.selected_period
            if period is None:
                safe_notify("Bitte Jahr und Quartal wählen.", type="warning")
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
                safe_notify("Abrechnung nicht möglich: LEG-Zuweisung fehlt.", type="negative")
                return
            render_result(run, items, control_check, distribution)
            safe_notify(
                f"Abrechnung für {leg_options[leg_select.value]}, Q{quarter} {year} erstellt.",
                type="positive",
            )
            refresh_runs_table()

        def confirm_rates_then_run_billing() -> None:
            """Show the currently configured billing rates for confirmation,
            then run (or re-run) billing for the selected LEG/period.

            The actual amounts get frozen onto each `BillingRunItem` the
            moment the run is created (see `app.domain.billing`) -- this
            step exists purely so a wrong rate is caught *before* that
            freeze happens, since a later correction in Einstellungen can
            no longer change what has already been billed.

            Returns:
                None.
            """
            if leg_select.value is None:
                ui.notify("Bitte eine LEG wählen.", type="warning")
                return
            if selector.selected_period is None:
                ui.notify("Bitte Jahr und Quartal wählen.", type="warning")
                return

            with connection_scope() as connection:
                settings = settings_repo.get_settings(connection)

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Aktuell hinterlegte Ansätze prüfen").classes("text-lg font-bold")
                ui.label(
                    "Diese Werte werden mit der Rechnung fest verrechnet. Eine "
                    "spätere Korrektur in den Einstellungen wirkt sich nur auf "
                    "künftige Abrechnungen aus, nie rückwirkend auf diesen Lauf."
                ).classes("text-caption text-grey-6")
                with ui.column().classes("gap-0 mt-2"):
                    ui.label(f"Energiepreis: {settings.price_rp_per_kwh:.2f} Rp./kWh")
                    ui.label(
                        f"Verwaltungsaufwand Bezug: "
                        f"{settings.verwaltungsaufwand_bezug_rp_per_kwh:.4f} Rp./kWh"
                    )
                    ui.label(
                        f"Verwaltungsaufwand Einspeisung: "
                        f"{settings.verwaltungsaufwand_einspeisung_rp_per_kwh:.4f} Rp./kWh"
                    )
                    ui.label(
                        f"Kosten Papierrechnung: {settings.papierrechnung_rappen / 100:.2f} CHF"
                    )

                def confirmed() -> None:
                    dialog.close()
                    run_billing()

                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    ui.link("Einstellungen anpassen", "/einstellungen").classes("self-center")
                    ui.button("Ansätze sind korrekt -- Rechnung erstellen", on_click=confirmed)
            dialog.open()

        run_button.on_click(confirm_rates_then_run_billing)

        ui.label("Bisherige Läufe").classes("text-lg font-bold mt-6")
        refresh_runs_table()
