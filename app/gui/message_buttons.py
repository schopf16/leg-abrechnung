"""A button per due baustein -- or the date it was settled.

The administrator's problem, in their words: *"ich möchte verhindern dass die
mails zweimal rausgehen. ich möchte einen button haben zum starten, nach
erfolgreichem senden möchte ich dann das datum sehen damit ich weiss dieser
person habe ich das schon einmal gesendet."*

So the button **becomes** the date, and `person_message_log` is what the
states are read from -- never a flag on the tracker.

Three states, because this feature arrives on a deployment where most
participants were already written to on paper: not settled (a send button and
a way to mark it done without sending), really sent (the date, and a quiet
"nochmals senden" that asks first), or marked by hand (the date, labelled
"von Hand", with the mark removable in one click).

Marking is **not** guarded by a question, deliberately: it sends nothing and
it can be taken back, so an undo is the better answer than a confirmation.
A send cannot be taken back, which is why that one asks.
"""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.message_templates import DueMessage, mark_done
from app.formatting import format_date
from app.gui.message_send_dialog import open_message_send_dialog
from app.gui.safe_notify import safe_notify
from app.models import person_message_log as log_repo
from app.models.person import Person

#: The small grey controls beside a settled baustein.
_QUIET = "dense flat size=sm color=grey-7"


def group_by_step(due: list[DueMessage]) -> dict[str, list[DueMessage]]:
    """Sort the due bausteine under the step each one hangs off.

    The card renders them **on the row of their own step**. They used to sit
    in a column of their own, which started at the top of the card while the
    steps did too -- so "Bei der BKW angemeldet" came out level with
    "Einteilung in LEG" and the two columns read as two unrelated tables.
    """
    grouped: dict[str, list[DueMessage]] = {}
    for message in due:
        grouped.setdefault(message.template.step, []).append(message)
    return grouped


def render_step_messages(
    person: Person,
    due: list[DueMessage],
    occasion: str,
    *,
    on_changed: Optional[Callable[[], None]] = None,
) -> None:
    """Render the controls for one step's bausteine, inline on that step's row."""
    for message in due:
        _render_one(person, message, occasion, on_changed)


def _render_one(
    person: Person,
    message: DueMessage,
    occasion: str,
    on_changed: Optional[Callable[[], None]],
) -> None:
    """One baustein, in whichever of the three states it is in."""
    name = message.template.name
    if not message.was_sent:
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.button(
                f"{name} senden",
                on_click=lambda: open_message_send_dialog(person, message, occasion, on_sent=on_changed),
            ).props("dense flat color=primary")
            if message.days_waiting is not None:
                # Why this button is here at all: the step has been open
                # longer than the baustein's deadline.
                ui.label(f"seit {message.days_waiting} Tagen offen").classes("text-caption text-grey-6")
            ui.button(
                "erledigt ohne Versand",
                on_click=lambda: _mark(person, message, occasion, on_changed),
            ).props(_QUIET)
        return

    suffix = " (von Hand)" if message.by_hand else ""
    with ui.row().classes("items-center gap-2 no-wrap"):
        ui.label(f"{name}: {format_date(message.sent_on)}{suffix}").classes("text-caption text-grey-7")
        ui.button(
            "senden" if message.by_hand else "nochmals senden",
            on_click=lambda: _ask_again(person, message, occasion, on_changed),
        ).props(_QUIET)
        if message.by_hand:
            ui.button(
                "Markierung entfernen",
                on_click=lambda: _unmark(person, message, on_changed),
            ).props(_QUIET)


def _mark(
    person: Person,
    message: DueMessage,
    occasion: str,
    on_changed: Optional[Callable[[], None]],
) -> None:
    """Settle a baustein that was dealt with on paper, without sending."""
    with connection_scope() as connection:
        mark_done(connection, person.id, message.template, occasion)
    # notify before the refresh -- see app.gui.safe_notify for why
    safe_notify(f'"{message.template.name}" als erledigt markiert, ohne Versand.', type="positive")
    if on_changed:
        on_changed()


def _unmark(person: Person, message: DueMessage, on_changed: Optional[Callable[[], None]]) -> None:
    """Take back a mark, so the send button comes back."""
    if message.log is None or message.log.id is None:
        return
    with connection_scope() as connection:
        log_repo.delete(connection, message.log.id)
    safe_notify("Markierung entfernt.", type="warning")
    if on_changed:
        on_changed()


def _ask_again(
    person: Person,
    message: DueMessage,
    occasion: str,
    on_changed: Optional[Callable[[], None]],
) -> None:
    """Ask before offering a send of something already settled."""
    settled = (
        f"Am {format_date(message.sent_on)} ohne Versand als erledigt markiert."
        if message.by_hand
        else f"Bereits am {format_date(message.sent_on)} gesendet."
    )
    with ui.dialog() as confirm, ui.card():
        ui.label(f'"{message.template.name}" an {person.display_name} senden?').classes("text-body1")
        ui.label(settled).classes("text-caption text-grey-7")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Abbrechen", on_click=confirm.close).props("flat")

            def again() -> None:
                """Close the question and show the mail."""
                confirm.close()
                open_message_send_dialog(person, message, occasion, on_sent=on_changed)

            ui.button("Mail öffnen", on_click=again)
    confirm.open()
