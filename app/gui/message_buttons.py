"""A button per due baustein -- or the date it already went out.

The administrator's problem, in their words: *"ich möchte verhindern dass die
mails zweimal rausgehen. ich möchte einen button haben zum starten, nach
erfolgreichem senden möchte ich dann das datum sehen damit ich weiss dieser
person habe ich das schon einmal gesendet."*

So the button **becomes** the date. Sending again stays possible, as a quiet
second control that asks first -- `person_message_log` is what the two states
are read from, never a flag on the tracker.

One renderer for Aufnahmen and Austritte both: two would drift, and the
decision which to show is the same decision on both pages.
"""

from typing import Callable, Optional

from nicegui import ui

from app.domain.message_templates import DueMessage
from app.formatting import format_date
from app.gui.message_send_dialog import open_message_send_dialog
from app.models.person import Person


def render_due_messages(
    person: Person,
    due: list[DueMessage],
    occasion: str,
    *,
    on_sent: Optional[Callable[[], None]] = None,
) -> None:
    """Render one control per due baustein for this person."""
    if not due:
        return
    with ui.column().classes("gap-0 min-w-[240px]"):
        for message in due:
            _render_one(person, message, occasion, on_sent)


def _render_one(
    person: Person,
    message: DueMessage,
    occasion: str,
    on_sent: Optional[Callable[[], None]],
) -> None:
    """Either the send button, or the date plus a way to send again."""
    name = message.template.name
    if not message.was_sent:
        with ui.row().classes("items-center gap-1"):
            ui.button(
                f"{name} senden",
                on_click=lambda: open_message_send_dialog(person, message, occasion, on_sent=on_sent),
            ).props("dense flat color=primary")
            if message.days_waiting is not None:
                # Why this button is here at all: the step has been open
                # longer than the baustein's deadline.
                ui.label(f"seit {message.days_waiting} Tagen offen").classes("text-caption text-grey-6")
        return
    with ui.row().classes("items-center gap-2"):
        ui.label(f"{name}: {format_date(message.sent_on)}").classes("text-caption text-grey-7")
        ui.button(
            "nochmals senden",
            on_click=lambda: _ask_again(person, message, occasion, on_sent),
        ).props("dense flat size=sm color=grey-7")


def _ask_again(
    person: Person,
    message: DueMessage,
    occasion: str,
    on_sent: Optional[Callable[[], None]],
) -> None:
    """Ask before offering a second send of something already sent."""
    with ui.dialog() as confirm, ui.card():
        ui.label(f'"{message.template.name}" nochmals an {person.display_name} senden?').classes("text-body1")
        ui.label(f"Bereits gesendet am {format_date(message.sent_on)}.").classes("text-caption text-grey-7")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Abbrechen", on_click=confirm.close).props("flat")

            def again() -> None:
                """Close the question and show the mail once more."""
                confirm.close()
                open_message_send_dialog(person, message, occasion, on_sent=on_sent)

            ui.button("Mail öffnen", on_click=again)
    confirm.open()
