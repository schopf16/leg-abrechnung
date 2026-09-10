"""receivables page: every Person's running account balance, a camt.053/
camt.054 bank statement import with automatic (QRR) and suggested
(IBAN/name/customer number) reconciliation, and a persistent queue of not-yet-
resolved bank transactions so nothing imported is ever silently lost.

Display sign convention: `account_entry.get_balance_rappen` uses the same
internal convention as `BillingRunItem.net_amount_rappen` (positive =
owed to the LEG). This page negates it for display exactly once, to match
how Michael thinks about it: a positive balance shown here means a credit/
overpayment, negative means the person still owes money. Never negate
anywhere else.
"""

import tempfile
from datetime import date
from pathlib import Path
from typing import Optional

from nicegui import events, ui

from app.db.connection import connection_scope
from app.domain import bank_reconciliation, dunning, person_ledger
from app.domain.iban_validation import format_iban
from app.gui.invoice_detail import open_invoice_detail
from app.gui.navigation import page_frame
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.importers.base import ImportValidationError
from app.importers.camt_parser import ParsedBankTransaction, parse_camt_file
from app.models import account_entry as account_entry_repo
from app.models import bank_transaction as bank_transaction_repo
from app.models import billing_run as billing_run_repo
from app.models import person as person_repo
from app.models import person_offboarding as person_offboarding_repo
from app.models.bank_transaction import BankTransaction
from app.models.person import Person

PRINT_COLUMNS = [
    ("Kunden-Nr.", "customer_number"),
    ("Name", "name"),
    ("Saldo (CHF)", "balance"),
]

#: Sentinel select-option values for the bank-transaction resolution
#: dropdowns, distinct from any real person id.
_LEAVE_OPEN = "leave_open"
_IGNORE = "ignore"


def _display_balance_chf(balance_rappen: int) -> float:
    """Negate + convert an internal balance to the GUI-displayed CHF value.

    Args:
        balance_rappen: Internal balance (positive = owed to the LEG), see
            `app.models.account_entry`'s sign-convention glossary.

    Returns:
        The value as Michael reads it: positive = Guthaben, negative =
        Schulden.
    """
    return -balance_rappen / 100


def _balance_color_class(balance_rappen: int) -> str:
    """CSS text-color class for a displayed balance.

    Args:
        balance_rappen: Internal balance.

    Returns:
        A Quasar/Tailwind text-color class name.
    """
    displayed = _display_balance_chf(balance_rappen)
    if displayed > 0:
        return "text-positive"
    if displayed < 0:
        return "text-negative"
    return ""


@ui.page("/receivables")
def receivables_page() -> None:
    """Render the receivables overview, bank-import assistant and open-items queue.

    Returns:
        None.
    """
    with page_frame("/receivables", "Debitoren"):
        ui.label(
            "Übersicht, wer der LEG etwas schuldet oder ein Guthaben hat. "
            "Ein importierter Kontoauszug ordnet Zahlungen automatisch der "
            "richtigen Rechnung zu, wo das anhand der QR-Referenz eindeutig "
            "möglich ist -- alles andere wird nur als Vorschlag angezeigt."
        ).classes("text-body2 text-grey-8")

        with ui.row().classes("w-full items-center gap-4 mt-2"):
            search_input = ui.input("Suche (Name, Kunden-Nr.)").classes("w-full max-w-md").props(
                "debounce=300 clearable"
            )
            only_forderung_switch = ui.switch("Nur offene Forderungen")
            only_guthaben_switch = ui.switch("Nur Guthaben")
            only_dunning_switch = ui.switch("Nur fällige Mahnungen")
            only_offboarding_switch = ui.switch("Nur laufende Austritte")
            render_print_button(
                heading="Debitoren",
                get_columns=lambda: PRINT_COLUMNS,
                get_rows=lambda: [_print_row(p, balance) for p, balance in visible_entries],
                get_filter_description=lambda: _filter_description(),
            )

        list_container = ui.column().classes("w-full gap-2 mt-2")

        all_entries: list[tuple[Person, int]] = []
        visible_entries: list[tuple[Person, int]] = []
        due_dunning_person_ids: set[int] = set()
        running_offboarding_person_ids: set[int] = set()

        def _print_row(person: Person, balance_rappen: int) -> dict:
            return {
                "customer_number": person.formatted_customer_number,
                "name": person.display_name,
                "balance": f"{_display_balance_chf(balance_rappen):.2f}",
            }

        def _filter_description() -> Optional[str]:
            parts = []
            if search_input.value:
                parts.append(f'Suche: "{search_input.value.strip()}"')
            if only_forderung_switch.value:
                parts.append("nur offene Forderungen")
            if only_guthaben_switch.value:
                parts.append("nur Guthaben")
            if only_dunning_switch.value:
                parts.append("nur fällige Mahnungen")
            if only_offboarding_switch.value:
                parts.append("nur laufende Austritte")
            return ", ".join(parts) if parts else None

        def render_person_card(person: Person, balance_rappen: int) -> None:
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center gap-6 flex-wrap"):
                    with ui.column().classes("gap-0 min-w-[220px]"):
                        ui.label(person.display_name).classes("font-bold")
                        ui.label(f"Kunden-Nr. {person.formatted_customer_number}").classes(
                            "text-caption text-grey-6"
                        )
                    ui.label(f"{_display_balance_chf(balance_rappen):.2f} CHF").classes(
                        "text-lg font-bold ml-auto " + _balance_color_class(balance_rappen)
                    )
                    ui.button("Details", on_click=lambda p=person: open_person_detail(p)).props(
                        "dense flat"
                    )

        def apply_filter() -> None:
            nonlocal visible_entries
            needle = (search_input.value or "").strip().lower()

            def matches(person: Person, balance_rappen: int) -> bool:
                if needle and needle not in person.display_name.lower() and needle not in str(
                    person.customer_number or ""
                ):
                    return False
                if only_forderung_switch.value and not (balance_rappen > 0):
                    return False
                if only_guthaben_switch.value and not (balance_rappen < 0):
                    return False
                if only_dunning_switch.value and person.id not in due_dunning_person_ids:
                    return False
                if only_offboarding_switch.value and person.id not in running_offboarding_person_ids:
                    return False
                return True

            visible_entries = [(p, s) for p, s in all_entries if matches(p, s)]
            list_container.clear()
            with list_container:
                if not visible_entries:
                    ui.label("Keine Einträge für diesen Filter.").classes("text-grey-6")
                for person, balance_rappen in visible_entries:
                    render_person_card(person, balance_rappen)

        def refresh_persons() -> None:
            nonlocal all_entries, due_dunning_person_ids, running_offboarding_person_ids
            with connection_scope() as connection:
                persons = person_repo.list_all(connection)
                saldi = account_entry_repo.get_all_saldi(connection)
                due_dunning_person_ids = {
                    c.person.id for c in dunning.list_due_dunnings(connection)
                }
                running_offboarding_person_ids = {
                    o.person_id for o in person_offboarding_repo.list_in_progress(connection)
                }
            all_entries = [(p, saldi.get(p.id, 0)) for p in persons]
            apply_filter()

        search_input.on_value_change(lambda _: apply_filter())
        only_forderung_switch.on_value_change(lambda _: apply_filter())
        only_dunning_switch.on_value_change(lambda _: apply_filter())
        only_offboarding_switch.on_value_change(lambda _: apply_filter())
        only_guthaben_switch.on_value_change(lambda _: apply_filter())

        # -- Person detail dialog -------------------------------------------------
        def open_person_detail(person: Person) -> None:
            with connection_scope() as connection:
                ledger_entries = person_ledger.list_ledger_entries(connection, person.id)
                balance_rappen = account_entry_repo.get_balance_rappen(connection, person.id)

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
                ui.label(person.display_name).classes("text-lg font-bold")
                ui.label(f"Saldo: {_display_balance_chf(balance_rappen):.2f} CHF").classes(
                    "font-bold " + _balance_color_class(balance_rappen)
                )
                ui.label(
                    "Zeitliche Übersicht, wie dieser Saldo zustande kommt: gestellte "
                    "Rechnungen/Gutschriften, versendete Mahnungen und verbuchte "
                    "Zahlungen."
                ).classes("text-caption text-grey-6")
                ui.separator()
                if ledger_entries:
                    with ui.column().classes("w-full gap-1 max-h-96 overflow-auto"):
                        for ledger_entry in ledger_entries:
                            with ui.row().classes("w-full items-center justify-between text-body2 border-b py-1"):
                                with ui.column().classes("gap-0"):
                                    ui.label(f"{ledger_entry.entry_date} -- {ledger_entry.description}")
                                with ui.row().classes("items-center gap-2"):
                                    if ledger_entry.amount_rappen is not None:
                                        ui.label(
                                            f"{_display_balance_chf(ledger_entry.amount_rappen):+.2f} CHF"
                                        ).classes(_balance_color_class(ledger_entry.amount_rappen))
                                    if ledger_entry.billing_run_item_id is not None:
                                        ui.button(
                                            "Details ansehen",
                                            on_click=lambda item_id=ledger_entry.billing_run_item_id: open_invoice_detail(item_id),
                                        ).props("dense flat")
                else:
                    ui.label("Noch keine Rechnungen, Mahnungen oder Zahlungen erfasst.").classes("text-grey-6")

                ui.separator()
                ui.label("Buchung manuell erfassen").classes("font-bold mt-2")
                amount_input = ui.number(
                    "Betrag (CHF) -- positiv = Guthaben/Zahlung, negativ = Nachbelastung", format="%.2f"
                ).classes("w-full")
                note_input = ui.input("Notiz (z.B. Barzahlung)").classes("w-full")

                def save_manual_entry() -> None:
                    if amount_input.value in (None, ""):
                        safe_notify("Bitte einen Betrag eingeben.", type="warning")
                        return
                    # GUI convention is negated relative to storage -- see
                    # module docstring: a positive value typed here (a
                    # Guthaben/payment) must reduce the internal balance.
                    amount_rappen = -round(float(amount_input.value) * 100)
                    with connection_scope() as connection:
                        account_entry_repo.create(
                            connection, person_id=person.id, kind="korrektur",
                            amount_rappen=amount_rappen,
                            booked_at=date.today().isoformat(),
                            note=note_input.value or "",
                        )
                    safe_notify("Buchung erfasst.", type="positive")
                    dialog.close()
                    refresh_persons()

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    ui.button("Buchen", on_click=save_manual_entry)
            dialog.open()

        # -- Bank statement import -------------------------------------------------
        ui.separator().classes("my-4")
        ui.label("Kontoauszug/Zahlungsavis importieren").classes("text-lg font-bold")
        ui.label(
            "camt.053 (Kontoauszug) oder camt.054 (QR-Zahlungsavis), .xml. "
            "Automatisch zugeordnete Zahlungen werden sofort gebucht, alles "
            "andere muss hier bestätigt werden, bevor es verbucht wird."
        ).classes("text-body2 text-grey-8")

        import_result_column = ui.column().classes("w-full gap-2 mt-2")
        pending: list[tuple[ParsedBankTransaction, "bank_reconciliation.MatchResult"]] = []
        pending_resolution_selects: list[ui.select] = []
        pending_source_format = {"value": "camt053"}
        pending_filename = {"value": ""}

        async def handle_upload(event: events.UploadEventArguments) -> None:
            data = await event.file.read()
            tmp_dir = Path(tempfile.mkdtemp(prefix="leg_bank_import_"))
            tmp_path = tmp_dir / event.file.name
            tmp_path.write_bytes(data)
            try:
                with connection_scope() as connection:
                    parse_result = parse_camt_file(tmp_path)
                    nonlocal pending
                    pending = [
                        (tx, bank_reconciliation.find_match(connection, tx))
                        for tx in parse_result.transactions
                    ]
            except ImportValidationError as exc:
                safe_notify(f"Import fehlgeschlagen: {exc}", type="negative")
                return
            finally:
                tmp_path.unlink(missing_ok=True)
                tmp_dir.rmdir()

            pending_source_format["value"] = parse_result.source_format
            pending_filename["value"] = event.file.name
            render_import_preview(parse_result)

        def render_import_preview(parse_result) -> None:
            import_result_column.clear()
            pending_resolution_selects.clear()
            with connection_scope() as connection:
                person_options = {p.id: p.display_name for p in person_repo.list_all(connection)}

            with import_result_column:
                if parse_result.warnings:
                    with ui.card().classes("w-full bg-warning bg-opacity-10"):
                        for warning in parse_result.warnings:
                            ui.label(f"⚠ {warning}").classes("text-body2")

                auto_count = sum(1 for _, m in pending if m.status == "auto_matched")
                ui.label(f"{auto_count} automatisch zugeordnet (QR-Referenz), {len(pending) - auto_count} benötigen eine Entscheidung.").classes(
                    "text-body2"
                )

                for index, (tx, match) in enumerate(pending):
                    with ui.row().classes("w-full items-center gap-3 border-b py-1"):
                        reversal_flag = " ⚠ STORNO" if tx.is_reversal else ""
                        ui.label(
                            f"{tx.booking_date} -- {tx.credit_debit_indicator} "
                            f"{tx.amount_rappen / 100:.2f} CHF -- {tx.counterparty_name}{reversal_flag}"
                        ).classes("text-body2 flex-grow")

                        if match.status == "auto_matched":
                            person_name = person_options.get(match.matched_person_id, "?")
                            ui.label(f"✓ automatisch: {person_name}").classes("text-positive text-body2")
                            pending_resolution_selects.append(None)
                            continue

                        options = {_LEAVE_OPEN: "-- offen lassen --", _IGNORE: "Ignorieren"}
                        default_value = _LEAVE_OPEN
                        for rank, candidate in enumerate(match.candidates):
                            label = f"{candidate.person_name} ({candidate.confidence})"
                            options[candidate.person_id] = label
                            if rank == 0 and not tx.is_reversal:
                                default_value = candidate.person_id
                        select = ui.select(options, value=default_value, label="Zuordnung").classes(
                            "w-64"
                        )
                        pending_resolution_selects.append(select)

                ui.button("Import abschliessen", icon="check", on_click=lambda: commit_import(parse_result)).classes(
                    "mt-2"
                )

        def commit_import(parse_result) -> None:
            with connection_scope() as connection:
                batch_id = bank_transaction_repo.create_batch(
                    connection, filename=pending_filename["value"],
                    account_iban=parse_result.account_iban,
                    statement_from=parse_result.statement_from,
                    statement_to=parse_result.statement_to,
                    entry_count=len(pending),
                )
                # commit=False on every row: a statement import can cover
                # dozens of transactions in this one loop, and they all
                # belong to the same logical "import this file" action --
                # the enclosing connection_scope commits once at the end
                # instead of once per row (see app.models.account_entry.
                # create's `commit` parameter for the rationale).
                for (tx, match), select in zip(pending, pending_resolution_selects):
                    if match.status == "auto_matched":
                        bank_reconciliation.book_transaction(
                            connection, bank_import_batch_id=batch_id, transaction=tx,
                            source_format=pending_source_format["value"],
                            person_id=match.matched_person_id,
                            billing_run_item_id=match.matched_billing_run_item_id,
                            status="auto_matched", commit=False,
                        )
                        continue

                    chosen = select.value if select is not None else _LEAVE_OPEN
                    if chosen == _IGNORE:
                        bank_reconciliation.book_transaction(
                            connection, bank_import_batch_id=batch_id, transaction=tx,
                            source_format=pending_source_format["value"],
                            person_id=None, billing_run_item_id=None, status="ignored",
                            commit=False,
                        )
                    elif chosen == _LEAVE_OPEN:
                        bank_reconciliation.book_transaction(
                            connection, bank_import_batch_id=batch_id, transaction=tx,
                            source_format=pending_source_format["value"],
                            person_id=None, billing_run_item_id=None, status=match.status,
                            commit=False,
                        )
                    else:
                        matching_candidate = next(
                            (c for c in match.candidates if c.person_id == chosen), None
                        )
                        billing_run_item_id = matching_candidate.billing_run_item_id if matching_candidate else None
                        bank_reconciliation.book_transaction(
                            connection, bank_import_batch_id=batch_id, transaction=tx,
                            source_format=pending_source_format["value"],
                            person_id=chosen, billing_run_item_id=billing_run_item_id,
                            status="manually_matched", commit=False,
                        )

            safe_notify(f"Import abgeschlossen: {len(pending)} Buchung(en) verarbeitet.", type="positive")
            import_result_column.clear()
            pending.clear()
            pending_resolution_selects.clear()
            refresh_persons()
            refresh_open_transactions()
            refresh_recent_transactions()

        ui.upload(
            label="Datei auswählen (.xml)", auto_upload=True, on_upload=handle_upload
        ).props('accept=".xml"').classes("w-full max-w-md")

        # -- Persistent "offene Bank-Buchungen" queue --------------------------
        ui.separator().classes("my-4")
        ui.label("Offene Bank-Buchungen").classes("text-lg font-bold")
        ui.label(
            "Buchungen aus früheren Importen, die noch keiner Person zugeordnet "
            "oder bewusst ignoriert wurden."
        ).classes("text-body2 text-grey-8")
        open_tx_container = ui.column().classes("w-full gap-2 mt-2")

        def render_open_transaction_row(tx: BankTransaction) -> None:
            with connection_scope() as connection:
                person_options = {p.id: p.display_name for p in person_repo.list_all(connection)}

            with ui.row().classes("w-full items-center gap-3 border-b py-1"):
                reversal_flag = " ⚠ STORNO" if tx.is_reversal else ""
                ui.label(
                    f"{tx.booking_date} -- {tx.credit_debit_indicator} "
                    f"{tx.amount_rappen / 100:.2f} CHF -- {tx.counterparty_name}{reversal_flag}"
                ).classes("text-body2 flex-grow" + (" text-negative" if tx.is_reversal else ""))
                options = {_IGNORE: "Ignorieren", **person_options}
                select = ui.select(options, label="Person zuweisen").classes("w-64")

                def assign(select=select, tx=tx) -> None:
                    if not select.value:
                        safe_notify("Bitte eine Zuordnung wählen.", type="warning")
                        return
                    with connection_scope() as connection:
                        bank_reconciliation.resolve_open_transaction(
                            connection, tx.id,
                            person_id=None if select.value == _IGNORE else select.value,
                        )
                    safe_notify("Zuordnung gespeichert.", type="positive")
                    refresh_persons()
                    refresh_open_transactions()
                    refresh_recent_transactions()

                ui.button("Zuweisen", on_click=assign).props("dense")

        def render_matched_transaction_row(tx: BankTransaction) -> None:
            with connection_scope() as connection:
                person = person_repo.get(connection, tx.matched_person_id) if tx.matched_person_id else None
            with ui.row().classes("w-full items-center gap-3 border-b py-1"):
                ui.label(
                    f"{tx.booking_date} -- {tx.credit_debit_indicator} "
                    f"{tx.amount_rappen / 100:.2f} CHF -- {person.display_name if person else '?'} "
                    f"({tx.status})"
                ).classes("text-body2 flex-grow")

                def undo(tx=tx) -> None:
                    with connection_scope() as connection:
                        bank_reconciliation.undo_match(connection, tx.id)
                    safe_notify("Zuordnung aufgehoben.", type="positive")
                    refresh_persons()
                    refresh_open_transactions()
                    refresh_recent_transactions()

                ui.button("Zuordnung rückgängig machen", on_click=undo).props("dense flat")

        def refresh_open_transactions() -> None:
            with connection_scope() as connection:
                open_transactions = bank_transaction_repo.list_open(connection)

            open_tx_container.clear()
            with open_tx_container:
                if not open_transactions:
                    ui.label("Keine offenen Bank-Buchungen.").classes("text-grey-6")
                for tx in open_transactions:
                    render_open_transaction_row(tx)

        # -- Recently matched, in case one needs to be corrected ------------------
        ui.separator().classes("my-4")
        ui.label("Kürzlich zugeordnete Bank-Buchungen").classes("text-lg font-bold")
        recent_tx_container = ui.column().classes("w-full gap-2 mt-2")

        def refresh_recent_transactions() -> None:
            with connection_scope() as connection:
                recent = [
                    tx for tx in bank_transaction_repo.list_recent(connection)
                    if tx.status in ("auto_matched", "manually_matched")
                ]
            recent_tx_container.clear()
            with recent_tx_container:
                if not recent:
                    ui.label("Noch keine zugeordneten Buchungen.").classes("text-grey-6")
                for tx in recent:
                    render_matched_transaction_row(tx)

        refresh_persons()
        refresh_open_transactions()
        refresh_recent_transactions()
