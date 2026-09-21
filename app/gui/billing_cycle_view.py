"""The guided billing run: six steps, four control points, one gate.

Renders `app.models.billing_cycle` the way the Aufnahme and Austritt
pages render their trackers -- the fixed step list with a date against
each -- plus the part that has no equivalent there: the control points
from `app.domain.billing_checks`, which decide whether the quarter may be
computed at all.

Kept out of `app.gui.pages.billing` so that page stays about running a
billing and this stays about tracking one; it takes the two actions that
belong to the page (compute all LEGs, send the invoice emails) as
callbacks.
"""

from datetime import date
from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.billing_checks import (
    ControlPoint,
    control_points_passed,
    list_paper_invoices,
    run_control_points,
)
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.models import billing_cycle as billing_cycle_repo
from app.models import billing_run as billing_run_repo
from app.models.billing_cycle import STEPS, BillingCycle

#: Columns of the printable paper-invoice list.
PAPER_COLUMNS = [
    ("Name", "person_name"),
    ("Adresse", "address"),
    ("LEG", "leg_name"),
    ("Betrag (CHF)", "amount"),
    ("Beleg", "document"),
]


def _readings_exist(connection, year: int, quarter: int) -> bool:
    """Whether the quarter has any readings at all.

    Args:
        connection: Open SQLite connection.
        year: Calendar year of the quarter.
        quarter: Quarter number, 1 to 4.

    Returns:
        `True` if at least one reading falls inside the quarter.
    """
    from app.domain.period import quarter_bounds

    start, end = quarter_bounds(year, quarter)
    row = connection.execute(
        "SELECT 1 FROM readings WHERE timestamp >= ? AND timestamp < ? LIMIT 1",
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    return row is not None


def _runs_exist(connection, year: int, quarter: int) -> bool:
    """Whether any billing run was computed for the quarter.

    Args:
        connection: Open SQLite connection.
        year: Calendar year of the quarter.
        quarter: Quarter number, 1 to 4.

    Returns:
        `True` if at least one run exists for this period.
    """
    return any(
        (run.period_year, run.period_quarter) == (year, quarter)
        for run in billing_run_repo.list_runs(connection)
    )


def _invoice_email_progress(connection, year: int, quarter: int) -> tuple[int, int]:
    """Count how many invoice emails of a quarter are still outstanding.

    Decided by `app.emailing.bulk_send.invoice_skip_reason`, the same
    rule the dispatch itself applies, rather than by re-listing the
    conditions here -- a recipient counted as outstanding who would then
    be skipped (or the reverse) would make the step lie.

    Args:
        connection: Open SQLite connection.
        year: Calendar year of the quarter.
        quarter: Quarter number, 1 to 4.

    Returns:
        `(outstanding, already_sent)`.
    """
    from app.emailing.bulk_send import invoice_skip_reason
    from app.models import person as person_repo

    persons = {p.id: p for p in person_repo.list_all(connection)}
    outstanding = 0
    sent = 0
    for run in billing_run_repo.list_runs(connection):
        if (run.period_year, run.period_quarter) != (year, quarter):
            continue
        for item in billing_run_repo.list_items(connection, run.id):
            if item.email_sent_at:
                sent += 1
            elif invoice_skip_reason(persons.get(item.person_id), item) is None:
                outstanding += 1
    return outstanding, sent


def _paper_rows(invoices) -> list[dict]:
    """Turn paper invoices into printable rows.

    Args:
        invoices: `PaperInvoice` instances.

    Returns:
        Row dicts matching `PAPER_COLUMNS`.
    """
    return [
        {
            "person_name": invoice.person_name,
            "address": invoice.address,
            "leg_name": invoice.leg_name,
            "amount": f"{invoice.amount_chf:.2f}",
            "document": invoice.document_path or "noch nicht erzeugt",
        }
        for invoice in invoices
    ]


def render_billing_cycle(
    year: int,
    quarter: int,
    *,
    on_compute: Callable[[], None],
    on_send_emails: Callable[[], None],
    on_changed: Callable[[], None],
) -> None:
    """Render the guided billing run for one quarter.

    Args:
        year: Calendar year of the quarter.
        quarter: Quarter number, 1 to 4.
        on_compute: Runs the all-LEGs billing and export. Called only
            once the control points allow it (or an override is on
            record).
        on_send_emails: Opens the invoice email dispatch.
        on_changed: Called after anything was recorded, so the page can
            rebuild itself.

    Returns:
        None.
    """
    with connection_scope() as connection:
        cycle = billing_cycle_repo.get_by_period(connection, year, quarter)
        points = run_control_points(connection, year, quarter)
        readings_present = _readings_exist(connection, year, quarter)
        runs_present = _runs_exist(connection, year, quarter)
        paper = list_paper_invoices(connection, year, quarter) if runs_present else []
        email_progress = _invoice_email_progress(connection, year, quarter) if runs_present else (0, 0)

    if cycle is None:
        ui.label(f"Für Q{quarter} {year} ist kein Rechnungslauf angelegt.").classes("text-grey-7")
        return

    # Recording an observation, not a claim: the readings either are there
    # or they are not, and the date says when they first were. Written
    # once and never withdrawn -- if they are deleted later the control
    # points say so, and "imported on that day" stays true.
    if readings_present and cycle.readings_imported_at is None:
        with connection_scope() as connection:
            cycle = billing_cycle_repo.mark_step(connection, cycle, "readings_imported_at")

    _render_progress(cycle)
    _render_control_points(cycle, points, on_changed)
    _render_steps(
        cycle,
        points,
        readings_present=readings_present,
        runs_present=runs_present,
        paper=paper,
        email_progress=email_progress,
        on_compute=on_compute,
        on_send_emails=on_send_emails,
        on_changed=on_changed,
    )


def _render_progress(cycle: BillingCycle) -> None:
    """Draw the one-line status of a cycle.

    Args:
        cycle: The cycle to describe.

    Returns:
        None.
    """
    done = sum(1 for attr, _ in STEPS if getattr(cycle, attr) is not None)
    with ui.row().classes("w-full items-center gap-3"):
        ui.label(f"Rechnungslauf {cycle.label}").classes("text-lg font-bold")
        if cycle.is_complete:
            ui.label("✓ abgeschlossen").classes("text-positive")
        else:
            _, label = cycle.current_step
            days = cycle.days_open()
            ui.label(f"Schritt {done + 1} von {len(STEPS)}: {label}").classes("text-grey-8")
            if days is not None and days > 0:
                ui.label(f"(seit {days} Tagen offen)").classes("text-caption text-grey-6")
    ui.linear_progress(value=done / len(STEPS), show_value=False).classes("w-full")


def _render_control_points(
    cycle: BillingCycle, points: list[ControlPoint], on_changed: Callable[[], None]
) -> None:
    """Draw the four control points and, if needed, the override.

    Args:
        cycle: The cycle they belong to.
        points: The freshly computed control points.
        on_changed: Page refresh.

    Returns:
        None.
    """
    passed = control_points_passed(points)
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-2"):
            ui.label("Kontrollpunkte").classes("text-lg font-bold")
            # Three states, not two: with an override on record the run is
            # no longer blocked, and saying otherwise while the compute
            # button plainly works reads as a malfunction.
            if passed:
                ui.label("✓ alle bestanden").classes("text-positive")
            elif cycle.was_overridden:
                ui.label("⚠ nicht erfüllt, bewusst umgangen").classes("text-warning")
            else:
                ui.label("⚠ Abrechnung gesperrt").classes("text-negative")
        ui.label(
            "Fehlende Messdaten eines einzelnen Messpunkts verfälschen auch die "
            "Rechnungen aller anderen -- der lokale Anteil jedes Intervalls wird "
            "unter den Anwesenden aufgeteilt."
        ).classes("text-caption text-grey-6")

        for point in points:
            with ui.row().classes("items-start gap-2 w-full"):
                ui.label("✓" if point.passed else "✗").classes(
                    "text-positive" if point.passed else "text-negative"
                )
                with ui.column().classes("gap-0"):
                    ui.label(point.label).classes("font-bold" if not point.passed else "")
                    ui.label(point.detail).classes("text-caption text-grey-7")
                    if point.affected and not point.passed:
                        shown = ", ".join(point.affected[:8])
                        more = "" if len(point.affected) <= 8 else f" … (+{len(point.affected) - 8})"
                        ui.label(f"Betroffen: {shown}{more}").classes("text-caption text-negative")

        # A recorded override never disappears: it is the reason a
        # quarter's figures may be questionable, and it has to stay
        # visible long after whoever decided it has forgotten.
        if cycle.was_overridden:
            ui.separator()
            ui.label("Kontrollpunkte wurden bewusst umgangen").classes("text-warning font-bold")
            ui.label(cycle.override_reason).classes("text-caption")
            if cycle.override_at:
                ui.label(f"Festgehalten am {cycle.override_at[:10]}").classes("text-caption text-grey-6")
        elif not passed:
            _render_override_button(cycle, on_changed)


def _render_override_button(cycle: BillingCycle, on_changed: Callable[[], None]) -> None:
    """Offer to proceed despite failing control points, against a reason.

    Args:
        cycle: The cycle to annotate.
        on_changed: Page refresh.

    Returns:
        None.
    """

    def open_dialog() -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
            ui.label("Trotzdem fortfahren").classes("text-lg font-bold")
            ui.label(
                "Die Abrechnung wird auf unvollständigen Daten gerechnet. Bitte "
                "festhalten, warum das in Ordnung ist -- die Begründung bleibt "
                "dauerhaft bei diesem Rechnungslauf sichtbar."
            ).classes("text-caption text-grey-7")
            reason = ui.textarea("Begründung").classes("w-full").props("autogrow")
            error = ui.label("").classes("text-negative text-caption")

            def confirm() -> None:
                try:
                    with connection_scope() as connection:
                        fresh = billing_cycle_repo.get(connection, cycle.id)
                        billing_cycle_repo.record_override(connection, fresh, reason.value or "")
                except ValueError as exc:
                    error.text = str(exc)
                    return
                dialog.close()
                safe_notify("Umgehung festgehalten.", type="warning")
                on_changed()

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Abbrechen", on_click=dialog.close).props("flat")
                ui.button("Umgehung festhalten", on_click=confirm).props("color=warning")
        dialog.open()

    ui.button("Trotzdem fortfahren", on_click=open_dialog).props("outline color=warning").classes("mt-2")


def _mark(cycle_id: int, attribute: str, on_changed: Callable[[], None]) -> None:
    """Record one step as done, today.

    Args:
        cycle_id: The cycle to update.
        attribute: The step's attribute name.
        on_changed: Page refresh.

    Returns:
        None.
    """
    with connection_scope() as connection:
        fresh = billing_cycle_repo.get(connection, cycle_id)
        billing_cycle_repo.mark_step(connection, fresh, attribute)
    on_changed()


def _unmark(cycle_id: int, attribute: str, on_changed: Callable[[], None]) -> None:
    """Undo one step's date, for a mis-click.

    Args:
        cycle_id: The cycle to update.
        attribute: The step's attribute name.
        on_changed: Page refresh.

    Returns:
        None.
    """
    with connection_scope() as connection:
        fresh = billing_cycle_repo.get(connection, cycle_id)
        setattr(fresh, attribute, None)
        billing_cycle_repo.update(connection, fresh)
    on_changed()


def _render_steps(
    cycle: BillingCycle,
    points: list[ControlPoint],
    *,
    readings_present: bool,
    runs_present: bool,
    paper: list,
    email_progress: tuple[int, int],
    on_compute: Callable[[], None],
    on_send_emails: Callable[[], None],
    on_changed: Callable[[], None],
) -> None:
    """Draw the six steps, each with whatever action moves it forward.

    Args:
        cycle: The cycle being worked on.
        points: The freshly computed control points.
        readings_present: Whether the quarter has readings.
        runs_present: Whether the quarter has computed runs.
        paper: The quarter's paper invoices.
        email_progress: `(outstanding, already_sent)` invoice emails.
        on_compute: Runs the all-LEGs billing and export.
        on_send_emails: Opens the invoice email dispatch.
        on_changed: Page refresh.

    Returns:
        None.
    """
    passed = control_points_passed(points)
    # Without readings there is nothing to compute, whatever the control
    # points say -- they have nothing to object to either.
    may_compute = readings_present and (passed or cycle.was_overridden)

    with ui.card().classes("w-full"):
        ui.label("Schritte").classes("text-lg font-bold")

        for attribute, label in STEPS:
            recorded: Optional[date] = getattr(cycle, attribute)
            with ui.row().classes("w-full items-center gap-3 py-1"):
                ui.label("✓" if recorded else "○").classes("text-positive" if recorded else "text-grey-5")
                ui.label(label).classes("font-bold" if not recorded else "").style("min-width: 22rem")
                ui.label(recorded.strftime("%d.%m.%Y") if recorded else "").classes(
                    "text-caption text-grey-7"
                ).style("min-width: 6rem")

                if recorded:
                    ui.button(
                        icon="undo", on_click=lambda a=attribute: _unmark(cycle.id, a, on_changed)
                    ).props("flat dense").tooltip("Datum wieder entfernen")
                    continue

                _render_step_action(
                    cycle,
                    attribute,
                    may_compute=may_compute,
                    readings_present=readings_present,
                    runs_present=runs_present,
                    paper=paper,
                    email_progress=email_progress,
                    on_compute=on_compute,
                    on_send_emails=on_send_emails,
                    on_changed=on_changed,
                )

        # The check recorded on step 2 is a statement about the data as it
        # was that day. Data imported since can turn the control points
        # red again, and a tick that no longer holds is worse than none.
        if cycle.readings_checked_at is not None and not passed and not cycle.was_overridden:
            ui.separator()
            ui.label(
                f"⚠ Seit der Prüfung vom {cycle.readings_checked_at.strftime('%d.%m.%Y')} "
                "sind die Kontrollpunkte nicht mehr erfüllt -- die Messdaten haben sich geändert."
            ).classes("text-negative")

    if paper:
        _render_paper_list(paper)


def _render_step_action(
    cycle: BillingCycle,
    attribute: str,
    *,
    readings_present: bool,
    runs_present: bool,
    may_compute: bool,
    paper: list,
    email_progress: tuple[int, int],
    on_compute: Callable[[], None],
    on_send_emails: Callable[[], None],
    on_changed: Callable[[], None],
) -> None:
    """Draw the action belonging to one open step.

    Args:
        cycle: The cycle being worked on.
        attribute: The step's attribute name.
        readings_present: Whether the quarter has readings.
        runs_present: Whether the quarter has computed runs.
        may_compute: Whether the control points (or an override) allow
            computing.
        paper: The quarter's paper invoices.
        email_progress: `(outstanding, already_sent)` invoice emails.
        on_compute: Runs the all-LEGs billing and export.
        on_send_emails: Opens the invoice email dispatch.
        on_changed: Page refresh.

    Returns:
        None.
    """
    if attribute == "readings_imported_at":
        # Nothing to confirm by hand: whether readings are there is a
        # question the app can answer, and asking for an attestation would
        # only invite a tick that is not true. This step therefore only
        # ever shows as open when the data really is missing.
        ui.label("Für dieses Quartal liegen noch keine Messwerte vor.").classes("text-caption text-negative")
        ui.link("Zum Import", "/import").classes("text-caption")
        return

    if attribute == "readings_checked_at":
        button = (
            ui.button(
                "Kontrollpunkte übernehmen",
                on_click=lambda: _mark(cycle.id, attribute, on_changed),
            )
            .props("outline dense")
            .tooltip("Hält fest, dass die Kontrollpunkte oben heute geprüft wurden.")
        )
        if not readings_present:
            button.disable()
            button.tooltip("Erst möglich, wenn Messdaten für dieses Quartal vorliegen.")
        elif not may_compute:
            # The step means "the data was found sound", so it cannot be
            # recorded while a control point is red. Ticking it anyway
            # produced a date that then read as "checked and fine", and
            # the page went on to claim the data must have changed since.
            # The red case has its own route: record an override.
            button.disable()
            button.tooltip(
                "Erst möglich, wenn alle Kontrollpunkte grün sind -- oder die Umgehung festgehalten wurde."
            )
        return

    if attribute == "computed_at":
        button = ui.button("Abrechnung erstellen (alle LEGs)", on_click=on_compute).props("dense")
        if not may_compute:
            button.disable()
            button.tooltip("Erst möglich, wenn alle Kontrollpunkte grün sind.")
        return

    if attribute == "emails_sent_at":
        outstanding, sent = email_progress
        button = ui.button("Rechnungen versenden", on_click=on_send_emails).props("outline dense")
        if not runs_present:
            button.disable()
            button.tooltip("Erst nach dem Erstellen der Abrechnung möglich.")
            return
        ui.label(f"{sent} versendet, {outstanding} offen").classes("text-caption text-grey-7")
        done = ui.button(
            "Versand abgeschlossen", on_click=lambda: _mark(cycle.id, attribute, on_changed)
        ).props("flat dense")
        if outstanding:
            # Deliberately not self-ticking: the step may only be closed
            # once nothing is outstanding, and saying so is more useful
            # than quietly refusing.
            done.disable()
            done.tooltip(f"Noch {outstanding} Rechnung(en) nicht versendet.")
        return

    if attribute == "paper_invoices_sent_at":
        button = ui.button(
            f"{len(paper)} Beleg(e) gedruckt und verschickt",
            on_click=lambda: _mark(cycle.id, attribute, on_changed),
        ).props("outline dense")
        if not runs_present:
            button.disable()
            button.tooltip("Erst nach dem Erstellen der Abrechnung möglich.")
        return

    if attribute == "payouts_done_at":
        button = ui.button(
            "Auszahlungen ausgelöst", on_click=lambda: _mark(cycle.id, attribute, on_changed)
        ).props("outline dense")
        if not runs_present:
            button.disable()
            button.tooltip("Erst nach dem Erstellen der Abrechnung möglich.")


def _render_paper_list(paper: list) -> None:
    """Draw the list of documents that have to go out on paper.

    Args:
        paper: `PaperInvoice` instances.

    Returns:
        None.
    """
    rows = _paper_rows(paper)
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.label("Belege auf Papier").classes("text-lg font-bold")
            render_print_button(
                heading="Belege auf Papier",
                get_columns=lambda: PAPER_COLUMNS,
                get_rows=lambda: rows,
            )
        ui.label(
            "Diese Personen erhalten ihren Beleg per Post; der E-Mail-Versand "
            "überspringt sie. Eine Gutschrift geht genauso auf Papier raus wie "
            "eine Rechnung."
        ).classes("text-caption text-grey-6")
        ui.table(
            columns=[
                {"name": field, "label": label, "field": field, "align": "left"}
                for label, field in PAPER_COLUMNS
            ],
            rows=rows,
            row_key="person_name",
        ).classes("w-full")
