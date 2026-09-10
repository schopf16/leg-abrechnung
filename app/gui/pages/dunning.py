"""dunning page: shows everyone currently due for a dunning notice (see
`app.domain.dunning` for the escalation logic -- the LEG's own 2-stage
Reglement, no fees), lets Michael preview and send it, and keeps a sent-
history log.

Sending is always a deliberate, per-person click -- there is no "send all"
button, since a 2. dunning notice is the trigger point for an exclusion review
that must never happen as a side effect of a bulk action.
"""

from datetime import date

from nicegui import ui

from app.config import ConfigError, get_graph_config
from app.db.connection import connection_scope
from app.domain import dunning
from app.emailing import graph_client
from app.gui.navigation import page_frame
from app.gui.print_list import render_print_button
from app.gui.safe_notify import safe_notify
from app.models import dunning_log as dunning_log_repo
from app.models import person as person_repo
from app.models import person_offboarding as person_offboarding_repo
from app.models import settings as settings_repo
from app.models.person import Person


PRINT_COLUMNS = [
    ("Person", "person"), ("Kunden-Nr.", "customer_number"), ("Stufe", "level"), ("Betrag (CHF)", "betrag"),
]


def _print_row(candidate: "dunning.MahnKandidat") -> dict:
    """Convert one dunning candidate into a row dict for the printed table.

    Args:
        candidate: The candidate to convert.

    Returns:
        A dict with the fields required by `PRINT_COLUMNS`.
    """
    return {
        "person": candidate.person.display_name,
        "customer_number": candidate.person.formatted_customer_number,
        "level": str(candidate.level),
        "betrag": f"{candidate.total_open_rappen / 100:.2f}",
    }


@ui.page("/dunning")
def dunning_page() -> None:
    """Render the dunning overview and sent-history page.

    Returns:
        None.
    """
    with page_frame("/dunning", "Mahnwesen"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Personen, deren Rechnung(en) die Zahlungsfrist überschritten "
                "haben. 1. Mahnung gewährt eine neue Frist, 2. Mahnung löst die "
                "Ausschluss-Prüfung aus -- keine Mahngebühr auf irgendeiner Stufe."
            ).classes("text-body2 text-grey-8")
            render_print_button(
                heading="Mahnwesen",
                get_columns=lambda: PRINT_COLUMNS,
                get_rows=lambda: [_print_row(c) for c in current_candidates],
            )

        candidates_container = ui.column().classes("w-full gap-2 mt-2")
        current_candidates: list[dunning.DunningCandidate] = []

        def render_candidate_card(candidate: dunning.DunningCandidate) -> None:
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center gap-4 flex-wrap"):
                    with ui.column().classes("gap-0 min-w-[220px]"):
                        ui.label(candidate.person.display_name).classes("font-bold")
                        ui.label(
                            f"{len(candidate.items)} offene Position(en), Kunden-Nr. "
                            f"{candidate.person.formatted_customer_number}"
                        ).classes("text-caption text-grey-6")
                    ui.badge(f"Stufe {candidate.level}", color="warning" if candidate.level == 1 else "negative")
                    ui.label(f"{candidate.total_open_rappen / 100:.2f} CHF").classes("font-bold ml-auto")
                    ui.button(
                        "Vorschau & Senden", on_click=lambda c=candidate: open_send_dialog(c)
                    ).props("dense")

        def refresh_candidates() -> None:
            nonlocal current_candidates
            with connection_scope() as connection:
                candidates = dunning.list_due_dunnings(connection)
            current_candidates = candidates
            candidates_container.clear()
            with candidates_container:
                if not candidates:
                    ui.label("Aktuell ist niemand zu mahnen.").classes("text-grey-6")
                for candidate in candidates:
                    render_candidate_card(candidate)

        def open_prepare_offboarding_dialog(person: Person) -> None:
            """Offer to start the Austritts-/Ausschlussprozess for `person`.

            Never automatic -- shown only after a 2. dunning notice was just
            sent, and only creates a `person_offboarding` row on an
            explicit confirm click here (idempotent otherwise, see
            `person_offboarding_repo.start_for_person`).

            Args:
                person: The person whose 2. dunning notice was just sent.

            Returns:
                None.
            """
            with connection_scope() as connection:
                already_tracked = person_offboarding_repo.get_by_person(connection, person.id)
            if already_tracked is not None:
                return

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
                ui.label("Ausschluss-Prozess vorbereiten?").classes("text-lg font-bold")
                ui.label(
                    f"Die 2. Mahnung an {person.display_name} wurde soeben versendet. "
                    "Falls die Zahlung weiterhin ausbleibt, kann der Ausschluss-Prozess "
                    "gestartet werden (beendet die Mitgliedschaft, nie die offene "
                    "Forderung -- siehe „Debitoren“)."
                ).classes("text-body2")
                start_date = ui.input(
                    "Beschlossen am", value=date.today().isoformat()
                ).props("type=date").classes("w-full")

                def start() -> None:
                    with connection_scope() as connection:
                        person_offboarding_repo.start_for_person(
                            connection, person.id, reason="zahlungsverzug",
                            decided_at=date.fromisoformat(start_date.value),
                        )
                    dialog.close()
                    safe_notify("Ausschluss-Prozess gestartet -- weitere Schritte unter „Austritte“.", type="positive")

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Nicht jetzt", on_click=dialog.close).props("flat")
                    ui.link("Zu „Austritte“", "/offboardings").classes("self-center")
                    ui.button("Ausschluss-Prozess starten", on_click=start, color="negative")
            dialog.open()

        def open_send_dialog(candidate: dunning.DunningCandidate) -> None:
            with connection_scope() as connection:
                settings = settings_repo.get_settings(connection)
            subject, body = dunning.render_dunning_text(settings, candidate)
            channel = (
                "E-Mail (mit PDF-Anhang)"
                if candidate.person.contact_email.strip() and not candidate.person.paper_invoice
                else "nur PDF (zum Ausdrucken/Selbstversand -- keine E-Mail-Adresse oder Papierrechnung bevorzugt)"
            )

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
                ui.label(f"{candidate.level}. Mahnung an {candidate.person.display_name}").classes(
                    "text-lg font-bold"
                )
                ui.label(f"Versandkanal: {channel}").classes("text-caption text-grey-6")
                ui.separator()
                ui.label(f"Betreff: {subject}").classes("font-bold mt-2")
                ui.label(body).classes("text-body2 whitespace-pre-line border p-2 rounded")
                if candidate.level == 2:
                    ui.label(
                        "⚠ Diese Mahnung löst bei weiterer Nichtzahlung die "
                        "Ausschluss-Prüfung aus. Nach dem Versand kannst du den "
                        "Austrittsprozess unter „Austritte“ manuell starten."
                    ).classes("text-negative text-body2 mt-2")

                async def do_send() -> None:
                    send_button.disable()
                    config = None
                    if candidate.person.contact_email.strip() and not candidate.person.paper_invoice:
                        try:
                            config = get_graph_config()
                        except ConfigError as exc:
                            safe_notify(str(exc), type="negative")
                            send_button.enable()
                            return
                    try:
                        with connection_scope() as connection:
                            await dunning.send_dunning(connection, config, candidate)
                    except (graph_client.GraphAuthError, graph_client.GraphApiError) as exc:
                        safe_notify(str(exc), type="negative")
                        send_button.enable()
                        return
                    safe_notify(f"{candidate.level}. Mahnung an {candidate.person.display_name} versendet.", type="positive")
                    dialog.close()
                    refresh_candidates()
                    refresh_history()
                    if candidate.level == 2:
                        open_prepare_offboarding_dialog(candidate.person)

                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    ui.button("Abbrechen", on_click=dialog.close).props("flat")
                    send_button = ui.button("Senden", on_click=do_send)
            dialog.open()

        ui.separator().classes("my-4")
        ui.label("Versand-Historie").classes("text-lg font-bold")
        history_container = ui.column().classes("w-full gap-1 mt-2")

        def refresh_history() -> None:
            with connection_scope() as connection:
                logs = dunning_log_repo.list_all(connection)
                person_names = {p.id: p.display_name for p in person_repo.list_all(connection)}
            history_container.clear()
            with history_container:
                if not logs:
                    ui.label("Noch keine Mahnung versendet.").classes("text-grey-6")
                for log in logs:
                    name = person_names.get(log.person_id, f"Person #{log.person_id}")
                    sent_display = log.sent_at.replace("T", " ").split(".")[0]
                    with ui.row().classes("w-full justify-between text-body2 border-b py-1"):
                        ui.label(f"{sent_display} -- {name} -- Stufe {log.level}")
                        ui.label(f"{log.amount_rappen / 100:.2f} CHF")

        refresh_candidates()
        refresh_history()
